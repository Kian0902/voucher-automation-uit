"""
The desktop app's server: the pages, two kinds of input, the lookups and both agents in one process.

Built from demo/app.py. An XML invoice runs through src/main.py as before. A PNG screenshot of
the ERP list goes through Mistral OCR, the parser and the row agent in src/list_agent.py, and the
agent page shows the rows filling in one by one. One run at a time, in one state every page polls.

The pages are plain HTML files in app/pages/, one per sidebar entry, served by path. They share
the stylesheet and the scripts in app/static/. Every run, finished or failed, appends a record
to output/runs.jsonl for the dashboard (app/api_dashboard.py). The knowledge base page edits
the three report CSVs in place and reloads the lookups (app/api_knowledge.py). The settings
page edits the config file; its changes apply at the next start (app/api_settings.py).

Paths resolve from APP_ROOT: the repo root in development, the folder next to the executable
when packaged. Configuration comes from config.env in that folder when it exists, else from
.env.local in the repo.
"""

import copy
import json
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles


APP_DIR = Path(__file__).resolve().parent
PAGES_DIR = APP_DIR / "pages"
STATIC_DIR = APP_DIR / "static"


def find_app_root() -> Path:
    """
    The folder that holds config.env, data/, logs/ and output/.

    In development it is the repo root. In the built app it is the folder next to
    Fakturaassistent.app, since the executable itself sits inside the bundle at
    Fakturaassistent.app/Contents/MacOS/.

    Returns:
        Path: The app root.
    """

    if not getattr(sys, "frozen", False):
        return APP_DIR.parent

    executable = Path(sys.executable).resolve()
    parts = executable.parts
    for index, part in enumerate(parts):
        bundle_follows = index + 1 < len(parts) and parts[index + 1] == "Contents"
        if part.endswith(".app") and bundle_follows:
            return Path(*parts[:index])
    return executable.parent


APP_ROOT = find_app_root()
# our modules: the agents in src/, the API modules next to this file
sys.path.insert(0, str(APP_ROOT / "src"))
sys.path.insert(0, str(APP_DIR))

# the model keys must be in the environment before main.py is imported, since it reads them at import
CONFIG_FILE = APP_ROOT / "config.env"
if not CONFIG_FILE.is_file():
    CONFIG_FILE = APP_ROOT / ".env.local"
load_dotenv(CONFIG_FILE)

APP_TITLE = "Fakturaassistent"
REPORTS_DIR = APP_ROOT / "data" / "koststed_tables"
OUTPUT_DIR = APP_ROOT / "output"

from langchain_core.messages import HumanMessage

import run_log
run_log.LOGS_DIR = APP_ROOT / "logs"

import lookup_data
import main as agent_main
import list_agent
from erp_list import COLUMN_KEYS, to_csv, to_json
from lookup_tools import lookup_io, lookup_koststed, lookup_person
from mistral_ocr import build_client, ocr_image
from ocr_parser import parse_ocr_response
from xml_processor import parse_invoice_xml

import api_dashboard
api_dashboard.RECORDS_FILE = OUTPUT_DIR / "runs.jsonl"
api_dashboard.REPORTS_DIR = REPORTS_DIR

import api_knowledge
api_knowledge.REPORTS_DIR = REPORTS_DIR

import api_settings
api_settings.CONFIG_FILE = CONFIG_FILE
api_settings.STARTED_WITH = api_settings.current_environment()
api_settings.FOLDERS = [
    ("Appens mappe", APP_ROOT),
    ("Innstillinger", CONFIG_FILE),
    ("Rapportene", REPORTS_DIR),
    ("Logger", run_log.LOGS_DIR),
    ("Resultater", OUTPUT_DIR),
]


# the app owns the terminal, so the run logs stay quiet; full logs still go to logs/
agent_main.run_log.live_output = False
list_agent.run_log.live_output = False


app = FastAPI(title=APP_TITLE)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.include_router(api_dashboard.router)
app.include_router(api_knowledge.router)
app.include_router(api_settings.router)


# the reports are loaded at start and again after the knowledge base changed a file; a failure
# is shown in the window and blocks every run until a good file is in place
DATA_ERROR = None


def _load_lookups():
    """
    Load the three reports into memory and remember whether it worked.

    Returns:
        str | None: The error text when the reports could not be loaded, else None.
    """

    global DATA_ERROR
    try:
        lookup_data.load_tables(REPORTS_DIR)
        DATA_ERROR = None
    except (FileNotFoundError, ValueError) as error:
        DATA_ERROR = f"Oppslagsdataene kunne ikke lastes: {error}"
    return DATA_ERROR


_load_lookups()
api_knowledge.reload_tables = _load_lookups


# Norwegian display names for the label taxonomy
LABEL_NAMES = {
    0: "Ingen feil",
    1: "Kjøpers referanse",
    2: "Leverandør",
    3: "Ordrenummer",
    4: "Duplikatfaktura",
    5: "Fakturanummer",
    6: "KID",
    7: "Datofeil",
    8: "Formatfeil",
}

# Norwegian display names for the lookup tools
TOOL_NAMES = {
    "lookup_io": "Slår opp ordrenummer",
    "lookup_person": "Slår opp person",
    "lookup_koststed": "Slår opp koststed",
}

# Norwegian outcome lines for the lookup statuses
LOOKUP_OUTCOMES = {
    "found": "funnet",
    "found_unknown_koststed": "funnet, men koststedet er ukjent",
    "not_found": "ikke funnet, ukjent for Unit4",
    "not_in_catalog": "ikke i katalogen, kan være en utgått kode",
    "invalid_format": "ugyldig format",
    "exact": "eksakt treff",
    "partial": "delvis treff",
    "partial_relaxed": "delvis treff, ett ord matchet ikke",
    "none": "ingen treff",
    "too_many": "for mange treff",
}

# the pipeline boxes the page draws, per kind of input: key, title, one line under it
INVOICE_STAGES = [
    ("receive", "Mottak", "Fakturaen lastes opp"),
    ("extract", "Uttrekk", "Alle felter leses ut av XML-en"),
    ("identify", "Finn problemer", "Språkmodellen finner det som stopper fakturaen"),
    ("classify", "Klassifiser", "Hvert problem får sin kategori"),
    ("resolve", "Slå opp og foreslå", "Slår opp i rapportene og foreslår rettelser"),
]
LIST_STAGES = [
    ("receive", "Mottak", "Skjermbildet lastes opp"),
    ("ocr", "Lesing", "Mistral OCR leser tabellen i bildet"),
    ("parse", "Tolking", "Radene settes inn i listestrukturen"),
    ("resolve", "Slå opp per rad", "Hver rad får problemer, oppslag og koststed"),
]


# STATE

# the state the page polls, guarded by STATE_LOCK
_IDLE_STATE = {
    "status": "idle",   # idle | running | done | error
    "kind": None,       # faktura | liste
    "filename": None,
    "error": None,
    "duration_seconds": None,
    "stages": [],       # [{"key", "title", "description", "status", "verdict"}], status pending | active | done | skipped
    "lookups": [],      # invoice runs: the lookups the resolver asked for
    "problems": [],     # invoice runs: the result rows
    "rows": [],         # list runs: one entry per row of the screenshot
    "rows_done": 0,
    "saved": [],        # the files written by a list run
}

STATE = copy.deepcopy(_IDLE_STATE)
STATE_LOCK = threading.Lock()


def _run_is_active() -> bool:
    """
    Whether a run is in progress, for the knowledge base, which refuses writes meanwhile.

    Returns:
        bool: True while the status is "running".
    """

    with STATE_LOCK:
        return STATE["status"] == "running"


api_knowledge.run_is_active = _run_is_active


def _set_stage(stage_key: str, stage_status: str, verdict=None) -> None:
    """
    Update one stage of the diagram.

    Args:
        stage_key (str): The stage's key, for example "identify" or "ocr".
        stage_status (str): "pending", "active", "done" or "skipped".
        verdict (str | None): Verdict line under the stage, kept as is when None.
    """

    with STATE_LOCK:
        for stage in STATE["stages"]:
            if stage["key"] == stage_key:
                stage["status"] = stage_status
                if verdict is not None:
                    stage["verdict"] = verdict


def _reset_state(filename: str, kind: str, stage_plan: list) -> None:
    """
    Reset STATE for a new run.

    Args:
        filename (str): Display name of the uploaded file.
        kind (str): "faktura" or "liste".
        stage_plan (list): INVOICE_STAGES or LIST_STAGES.
    """

    global STATE
    stages = []
    for key, title, description in stage_plan:
        stages.append({"key": key, "title": title, "description": description, "status": "pending", "verdict": ""})

    with STATE_LOCK:
        STATE = copy.deepcopy(_IDLE_STATE)
        STATE["status"] = "running"
        STATE["kind"] = kind
        STATE["filename"] = filename
        STATE["stages"] = stages
        STATE["stages"][0]["status"] = "done"
        STATE["stages"][0]["verdict"] = filename
        STATE["stages"][1]["status"] = "active"


def _fail_run(message: str) -> None:
    """
    End the run with an error the page shows in the banner.

    Args:
        message (str): The Norwegian error text.
    """

    with STATE_LOCK:
        STATE["status"] = "error"
        STATE["error"] = message
    _record_run("error", message)


def _invoice_result(problems: list) -> str:
    """
    The result of a whole invoice, from the statuses of its problems.

    Args:
        problems (list[dict]): The result rows of the invoice run.

    Returns:
        str: "løst" when every problem is løst (or there was none), "må bekreftes" when the
             rest are suggestions, else "uløst". A problem without a status counts as uløst,
             since the resolver never reached it.
    """

    if len(problems) == 0:
        return "løst"
    all_resolved = True
    any_unresolved = False
    for problem in problems:
        if problem["status"] != "løst":
            all_resolved = False
        if problem["status"] != "løst" and problem["status"] != "må bekreftes":
            any_unresolved = True
    if all_resolved:
        return "løst"
    if any_unresolved:
        return "uløst"
    return "må bekreftes"


def _record_run(status: str, error) -> None:
    """
    Append the record of the current run for the dashboard.

    A list run gives one item per row the agent finished, so a run that failed halfway keeps
    the rows it had. An invoice run gives one item for the invoice when it finished, none when
    it failed. A record that cannot be written is reported on stderr and never fails the run.

    Args:
        status (str): "done" or "error".
        error (str | None): The error text of a failed run.
    """

    with STATE_LOCK:
        kind = STATE["kind"]
        filename = STATE["filename"]
        duration = STATE["duration_seconds"]
        rows = copy.deepcopy(STATE["rows"])
        problems = copy.deepcopy(STATE["problems"])

    items = []
    if kind == "liste":
        model = list_agent.run_log.model_used()
        for row in rows:
            if row["status"] != "done":
                continue
            items.append(api_dashboard.new_item(
                f"rad {row['row_number']}", row["resultat"], row["grunner"],
                row["tid_brukt"], row["tokens_brukt"], row["oppslag_brukt"],
            ))
    else:
        model = agent_main.run_log.model_used()
        if status == "done":
            tokens = agent_main.run_log.total_input_tokens() + agent_main.run_log.total_output_tokens()
            items.append(api_dashboard.new_item(
                "faktura", _invoice_result(problems), [], duration, tokens, agent_main.run_log.lookups_used,
            ))

    record = api_dashboard.new_record(kind, filename, status, error, duration, model, items)
    try:
        api_dashboard.append_record(record)
    except OSError as write_error:
        print(f"run record not written: {write_error}", file=sys.stderr)


# INVOICE RUNS

def _parse_uploaded_xml(xml_bytes: bytes) -> dict:
    """
    Parse uploaded XML bytes with the ordinary XML processor.

    The processor reads from a path, so the bytes go through a temporary file that is deleted
    right after parsing.

    Args:
        xml_bytes (bytes): The uploaded file content.

    Returns:
        dict: The extracted invoice.
    """

    temp_file = tempfile.NamedTemporaryFile(suffix=".xml", delete=False)
    temp_path = Path(temp_file.name)
    temp_file.write(xml_bytes)
    temp_file.close()

    try:
        invoice = parse_invoice_xml(temp_path)
    finally:
        temp_path.unlink()

    return invoice


def _sanitize_source_file(invoice: dict, filename: str) -> None:
    """
    Keep only the document type and number from the filename.

    Some example filenames also name the invoice's known problem, and the agent must never see
    that. Same rule as run_all_invoices in src/main.py.

    Args:
        invoice (dict): The extracted invoice, changed in place.
        filename (str): The original filename of the upload.
    """

    source_match = re.match(r"^(Faktura|Kreditnota)\s+([0-9-]+)", filename)
    if source_match is not None:
        invoice["source_file"] = source_match.group(1) + " " + source_match.group(2)
    else:
        if "source_file" in invoice:
            del invoice["source_file"]


def _lookup_outcome_text(result_text: str) -> str:
    """
    Map one tool result to a short Norwegian outcome line.

    Args:
        result_text (str): The tool result as JSON text.

    Returns:
        str: For example "eksakt treff" or "ikke funnet, ukjent for Unit4".
    """

    try:
        result = json.loads(result_text)
    except ValueError:
        return "feilet"

    status = result.get("status")
    if status is None:
        status = result.get("match_type")
    if status is None:
        return "ok"

    outcome = LOOKUP_OUTCOMES.get(status)
    if outcome is None:
        outcome = status

    # a value that is not even IO format must not read as "unknown to Unit4"
    if status == "not_found" and result.get("format_valid") is False:
        outcome = "ugyldig IO-format"

    # candidate counts make the person lookups concrete on screen
    persons = result.get("persons")
    if persons is not None and len(persons) > 1:
        outcome = outcome + f", {len(persons)} kandidater"

    return outcome


def _apply_identify_update(delta: dict) -> None:
    """
    Show the identification outcome and create the result rows.

    Args:
        delta (dict): The state update the identify_problems node returned.
    """

    problems = delta["problems"]

    if problems is None:
        _set_stage("identify", "done", "Modellen ga ikke et gyldig svar")
        _set_stage("classify", "skipped")
        _set_stage("resolve", "skipped")
        return

    if len(problems.problems) == 0:
        _set_stage("identify", "done", "Ingen problemer funnet")
        _set_stage("classify", "skipped")
        _set_stage("resolve", "skipped")
        return

    rows = []
    for problem in problems.problems:
        rows.append({
            "problem": problem.problem,
            "field": problem.field,
            "value": problem.value,
            "category": "",
            "suggestion": "",
            "why": "",
            "status": "",
        })

    problem_count = len(rows)
    if problem_count == 1:
        verdict = "1 problem funnet"
    else:
        verdict = f"{problem_count} problemer funnet"

    with STATE_LOCK:
        STATE["problems"] = rows

    _set_stage("identify", "done", verdict)
    _set_stage("classify", "active")


def _apply_classify_update(delta: dict):
    """
    Show the classification outcome and fill in the categories.

    Args:
        delta (dict): The state update the classify_problems node returned.

    Returns:
        The LabeledProblems object for the final log, or None.
    """

    labeled = delta["labeled_problems"]

    if labeled is None:
        _set_stage("classify", "done", "Modellen ga ikke et gyldig svar")
        _set_stage("resolve", "active")
        return None

    category_names = []
    for labeled_problem in labeled.problems:
        name = LABEL_NAMES.get(labeled_problem.label, f"Kategori {labeled_problem.label}")
        if name not in category_names:
            category_names.append(name)

    with STATE_LOCK:
        rows = STATE["problems"]
        for index, labeled_problem in enumerate(labeled.problems):
            if index < len(rows):
                category = LABEL_NAMES.get(labeled_problem.label, f"Kategori {labeled_problem.label}")
                rows[index]["category"] = category

    if len(category_names) == 1:
        verdict = "Kategori: " + category_names[0]
    else:
        verdict = "Kategorier: " + ", ".join(category_names)

    _set_stage("classify", "done", verdict)
    _set_stage("resolve", "active")
    return labeled


def _apply_resolve_update(delta: dict, previous_resolutions):
    """
    Record a requested lookup, or pick up the final resolutions.

    Args:
        delta (dict): The state update the resolve_problems node returned.
        previous_resolutions: The resolutions seen so far, normally None.

    Returns:
        The resolutions list when the resolver said it is done, otherwise previous_resolutions.
    """

    requested_tool = delta["tool"]

    if requested_tool != "none":
        display_name = TOOL_NAMES.get(requested_tool, requested_tool)
        with STATE_LOCK:
            STATE["lookups"].append({
                "name": display_name,
                "argument": delta["argument"],
                "outcome": None,
            })
        return previous_resolutions

    return delta["resolutions"]


def _apply_tool_update(delta: dict) -> None:
    """
    Fill in the outcome of the lookup that just ran.

    Args:
        delta (dict): The state update the execute_tool node returned.
    """

    history = delta["resolve_messages"]
    result_message = history[-1]
    outcome = _lookup_outcome_text(result_message.content)

    with STATE_LOCK:
        for lookup in reversed(STATE["lookups"]):
            if lookup["outcome"] is None:
                lookup["outcome"] = outcome
                break


def _clean_suggestion(suggestion: str) -> str:
    """
    Treat suggestions without letters or digits as empty.

    Unresolved problems sometimes get junk like ":" as the suggestion, which must not count as a
    real suggestion.

    Args:
        suggestion (str): The suggestion text from the resolver.

    Returns:
        str: The stripped suggestion, or "" when it holds no letters or digits.
    """

    stripped_suggestion = suggestion.strip()
    has_content = re.search(r"\w", stripped_suggestion) is not None
    if not has_content:
        return ""
    return stripped_suggestion


def _fill_resolution_rows(resolutions) -> int:
    """
    Fill suggestion, why and status into the result rows.

    Args:
        resolutions: The Resolution objects, one per problem, same order.

    Returns:
        int: How many problems were resolved.
    """

    resolved_count = 0
    with STATE_LOCK:
        rows = STATE["problems"]
        for index, resolution in enumerate(resolutions):
            suggestion = _clean_suggestion(resolution.suggestion)

            if resolution.resolved:
                status_text = "løst"
                resolved_count = resolved_count + 1
            elif suggestion != "":
                status_text = "må bekreftes"
            else:
                status_text = "uløst"

            if index < len(rows):
                rows[index]["suggestion"] = suggestion
                rows[index]["why"] = resolution.why
                rows[index]["status"] = status_text

    return resolved_count


def _finalize_invoice_run(labeled_problems, resolutions) -> None:
    """
    Close an invoice run: final verdicts, result rows, run log and status.

    Args:
        labeled_problems: The LabeledProblems object, or None.
        resolutions: The final Resolution list, or None.
    """

    # a lookup the loop never executed (the cap stopped it) must not look active
    with STATE_LOCK:
        for lookup in STATE["lookups"]:
            if lookup["outcome"] is None:
                lookup["outcome"] = "stoppet, maks antall oppslag nådd"

    if resolutions is None:
        with STATE_LOCK:
            resolve_status = None
            for stage in STATE["stages"]:
                if stage["key"] == "resolve":
                    resolve_status = stage["status"]
        if resolve_status == "active":
            _set_stage("resolve", "done", "Modellen ga ingen konklusjon")
    else:
        resolved_count = _fill_resolution_rows(resolutions)
        total_count = len(resolutions)
        _set_stage("resolve", "done", f"{resolved_count} av {total_count} løst")

    final_state = {"labeled_problems": labeled_problems, "resolutions": resolutions}
    agent_main.run_log.finish(final_state)
    agent_main.run_log.save_txt()

    with STATE_LOCK:
        STATE["status"] = "done"
        STATE["duration_seconds"] = agent_main.run_log.duration_seconds()
    _record_run("done", None)


def _run_invoice(xml_bytes: bytes, filename: str) -> None:
    """
    Run the invoice agent on one uploaded XML and update STATE live.

    Runs in a background thread. Every graph update is translated to stage statuses, verdicts
    and lookup lines the page can show.

    Args:
        xml_bytes (bytes): The uploaded XML file content.
        filename (str): The original filename, used for display and the sanitized source_file.
    """

    try:
        invoice = _parse_uploaded_xml(xml_bytes)
        _sanitize_source_file(invoice, filename)

        invoice_id = invoice.get("invoice_id")
        if invoice_id is None:
            extract_verdict = "Data hentet ut, fakturanummer mangler"
        else:
            extract_verdict = f"Faktura {invoice_id} hentet ut"
        _set_stage("extract", "done", extract_verdict)
        _set_stage("identify", "active")

        invoice_json = json.dumps(invoice, indent=2, ensure_ascii=False)

        agent_main.run_log.start(filename)

        initial_state = {
            "messages": [HumanMessage(content=invoice_json)],
            "problems": None,
            "labeled_problems": None,
            "resolve_messages": [],
            "tool": "none",
            "argument": "",
            "tool_calls_used": 0,
            "resolutions": None,
        }

        labeled_problems = None
        resolutions = None

        for update in agent_main.agent.stream(initial_state, stream_mode="updates"):
            if "identify_problems" in update:
                _apply_identify_update(update["identify_problems"])
            if "classify_problems" in update:
                labeled_problems = _apply_classify_update(update["classify_problems"])
            if "resolve_problems" in update:
                resolutions = _apply_resolve_update(update["resolve_problems"], resolutions)
            if "execute_tool" in update:
                _apply_tool_update(update["execute_tool"])

        _finalize_invoice_run(labeled_problems, resolutions)

    except Exception as error:
        _fail_run(f"Kjøringen feilet: {error}")


# LIST RUNS

def _row_for_page(row: dict) -> dict:
    """
    The page's view of one row: the ERP columns, the result fields and a status.

    Args:
        row (dict): A row from the parsed structure.

    Returns:
        dict: row_number, the eleven columns, funnet_koststed, resultat, begrunnelse, tid_brukt,
              tokens_brukt, oppslag_brukt, grunner and status "pending".
    """

    page_row = {"row_number": row["row_number"]}
    for key in COLUMN_KEYS:
        page_row[key] = row[key]
    page_row["funnet_koststed"] = []
    page_row["resultat"] = None
    page_row["begrunnelse"] = None
    page_row["tid_brukt"] = None
    page_row["tokens_brukt"] = None
    page_row["oppslag_brukt"] = None
    page_row["grunner"] = []
    page_row["status"] = "pending"
    return page_row


def _ocr_screenshot(image_bytes: bytes) -> dict:
    """
    Run Mistral OCR on uploaded PNG bytes through a temporary file.

    Args:
        image_bytes (bytes): The uploaded screenshot.

    Returns:
        dict: The raw OCR response.
    """

    temp_file = tempfile.NamedTemporaryFile(suffix=".png", delete=False)
    temp_path = Path(temp_file.name)
    temp_file.write(image_bytes)
    temp_file.close()

    try:
        client = build_client()
        raw_response = ocr_image(temp_path, client)
    finally:
        temp_path.unlink()

    return raw_response


def _resolve_rows(erp_list: dict) -> dict:
    """
    Run the row agent on every row of a parsed list, updating STATE after each row.

    Args:
        erp_list (dict): The parsed structure from ocr_parser.

    Returns:
        dict: The resolved list, as list_agent.resolve_list() builds it.
    """

    resolved_list = {
        "source_file": erp_list["source_file"],
        "category": erp_list["category"],
        "rows": [],
    }

    for index, row in enumerate(erp_list["rows"]):
        with STATE_LOCK:
            STATE["rows"][index]["status"] = "active"

        resolved_row = list_agent.resolve_one_row(row, erp_list["source_file"])
        resolved_list["rows"].append(resolved_row)

        with STATE_LOCK:
            page_row = STATE["rows"][index]
            page_row["funnet_koststed"] = resolved_row["funnet_koststed"]
            page_row["resultat"] = resolved_row["resultat"]
            page_row["begrunnelse"] = resolved_row["begrunnelse"]
            page_row["tid_brukt"] = resolved_row["tid_brukt"]
            page_row["tokens_brukt"] = resolved_row["tokens_brukt"]
            page_row["oppslag_brukt"] = resolved_row["oppslag_brukt"]
            page_row["grunner"] = resolved_row["grunner"]
            page_row["status"] = "done"
            STATE["rows_done"] = index + 1

    return resolved_list


def _save_list_outputs(raw_response: dict, resolved_list: dict, filename: str) -> list:
    """
    Write the raw OCR response, the resolved list and the CSV sheet to the output folder.

    Args:
        raw_response (dict): The OCR response, kept so a misread can be traced.
        resolved_list (dict): The resolved list from _resolve_rows().
        filename (str): The screenshot's filename; its stem names the files.

    Returns:
        list[Path]: The files written.
    """

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = Path(filename).stem

    ocr_path = OUTPUT_DIR / (stem + ".ocr.json")
    ocr_path.write_text(json.dumps(raw_response, indent=2, ensure_ascii=False), encoding="utf-8")

    json_path = OUTPUT_DIR / (stem + ".json")
    json_path.write_text(to_json(resolved_list), encoding="utf-8")

    # utf-8-sig: the byte order mark makes Excel read æøå correctly
    csv_path = OUTPUT_DIR / (stem + ".csv")
    csv_path.write_text(to_csv(resolved_list), encoding="utf-8-sig")

    return [ocr_path, json_path, csv_path]


def _run_list(image_bytes: bytes, filename: str) -> None:
    """
    OCR, parse and resolve one uploaded screenshot and update STATE live.

    Runs in a background thread.

    Args:
        image_bytes (bytes): The uploaded PNG.
        filename (str): The original filename, for display and the output names.
    """

    started = time.perf_counter()
    try:
        raw_response = _ocr_screenshot(image_bytes)
        _set_stage("ocr", "done", "Bildet er lest")
        _set_stage("parse", "active")

        try:
            erp_list = parse_ocr_response(raw_response, filename, None)
        except ValueError as error:
            _set_stage("parse", "done", "Tabellen kunne ikke tolkes")
            _set_stage("resolve", "skipped")
            _fail_run(f"Tabellen i skjermbildet kunne ikke tolkes: {error}")
            return

        page_rows = []
        for row in erp_list["rows"]:
            page_rows.append(_row_for_page(row))
        with STATE_LOCK:
            STATE["rows"] = page_rows

        row_count = len(page_rows)
        if row_count == 1:
            parse_verdict = "1 rad funnet"
        else:
            parse_verdict = f"{row_count} rader funnet"
        _set_stage("parse", "done", parse_verdict)
        _set_stage("resolve", "active")

        resolved_list = _resolve_rows(erp_list)

        label_counts = {}
        for resolved_row in resolved_list["rows"]:
            label = resolved_row["resultat"]
            label_counts[label] = label_counts.get(label, 0) + 1
        resolved_count = label_counts.get("løst", 0)
        _set_stage("resolve", "done", f"{resolved_count} av {row_count} løst")

        saved_paths = _save_list_outputs(raw_response, resolved_list, filename)

        with STATE_LOCK:
            STATE["status"] = "done"
            STATE["duration_seconds"] = time.perf_counter() - started
            STATE["saved"] = [str(path) for path in saved_paths]
        _record_run("done", None)

    except Exception as error:
        _fail_run(f"Kjøringen feilet: {error}")


# ROUTES

def _page(name: str) -> HTMLResponse:
    """
    Serve one of the pages in app/pages/.

    Args:
        name (str): The file's stem, such as "dashboard".

    Returns:
        HTMLResponse: The page as it stands on disk, so an edit shows on the next load.
    """

    page_path = PAGES_DIR / (name + ".html")
    return HTMLResponse(page_path.read_text(encoding="utf-8"))


@app.get("/")
def dashboard_page() -> HTMLResponse:
    """The dashboard, the first page the staff sees."""

    return _page("dashboard")


@app.get("/agents")
def agents_page() -> HTMLResponse:
    """The page that runs both agents."""

    return _page("agents")


@app.get("/knowledge")
def knowledge_page() -> HTMLResponse:
    """The knowledge base: the three reports."""

    return _page("knowledge")


@app.get("/settings")
def settings_page() -> HTMLResponse:
    """The settings page."""

    return _page("settings")


@app.get("/status")
def status() -> dict:
    """
    Snapshot of the current run for the page to render.

    Returns:
        dict: A copy of STATE plus data_error when the reports could not be loaded.
    """

    with STATE_LOCK:
        snapshot = copy.deepcopy(STATE)
    snapshot["data_error"] = DATA_ERROR
    return snapshot


@app.get("/search")
def search(query: str):
    """
    Manual search in the three reports for the support panel.

    Detects what was typed by format: 9 digits starting with 6 is an IO, 8 digits is a koststed
    (catalog entry plus its IOs), any other all-digit value gets a format hint, and everything
    else is treated as a person name.

    Args:
        query (str): The value typed in the search field.

    Returns:
        dict: kind is "io", "koststed", "person" or "ukjent", with the lookup results attached.
    """

    stripped_query = query.strip()

    if stripped_query == "":
        return {"kind": "ukjent", "query": stripped_query, "note": "Skriv inn et IO, et koststed eller et navn"}

    if re.fullmatch(r"6\d{8}", stripped_query) is not None:
        result = lookup_io(stripped_query)
        return {"kind": "io", "query": stripped_query, "result": result}

    if re.fullmatch(r"\d{8}", stripped_query) is not None:
        catalog = lookup_koststed(stripped_query)
        ios = lookup_data.lookup_ios_for_koststed(stripped_query)
        return {"kind": "koststed", "query": stripped_query, "catalog": catalog, "ios": ios}

    if re.fullmatch(r"\d+", stripped_query) is not None:
        if len(stripped_query) == 6:
            note = ("6 sifre ligner et ansattnummer. Ansattnummer kan ikke slås opp, "
                    "rapport A mangler den kolonnen")
        else:
            note = "Tallet er verken et IO (9 sifre, starter med 6) eller et koststed (8 sifre)"
        return {"kind": "ukjent", "query": stripped_query, "note": note}

    result = lookup_person(stripped_query)
    return {"kind": "person", "query": stripped_query, "result": result}


@app.post("/run")
async def run(request: Request, filename: str = "fil"):
    """
    Start a run on an uploaded file: an XML invoice or a PNG screenshot of the list.

    The file type is read from the filename's extension. The run happens in a background
    thread; the page follows it through /status.

    Args:
        request (Request): The request whose body is the raw file.
        filename (str): The original filename, from the query string.

    Returns:
        A confirmation, or an error message with a fitting status code.
    """

    with STATE_LOCK:
        already_running = STATE["status"] == "running"
    if already_running:
        return JSONResponse(
            {"message": "En kjøring pågår allerede, vent til den er ferdig"},
            status_code=409,
        )

    if DATA_ERROR is not None:
        return JSONResponse({"message": DATA_ERROR}, status_code=503)

    file_bytes = await request.body()
    if len(file_bytes) == 0:
        return JSONResponse({"message": "Ingen fil mottatt"}, status_code=400)

    suffix = Path(filename).suffix.lower()
    if suffix == ".xml":
        _reset_state(filename, "faktura", INVOICE_STAGES)
        worker = threading.Thread(target=_run_invoice, args=(file_bytes, filename), daemon=True)
    elif suffix == ".png":
        _reset_state(filename, "liste", LIST_STAGES)
        worker = threading.Thread(target=_run_list, args=(file_bytes, filename), daemon=True)
    else:
        return JSONResponse(
            {"message": "Filtypen støttes ikke. Slipp en XML-faktura eller et PNG-skjermbilde av listen"},
            status_code=400,
        )

    worker.start()
    return {"message": "startet"}


if __name__ == "__main__":
    # for development in a browser; the desktop window is app/launcher.py
    uvicorn.run(app, host="127.0.0.1", port=8001)

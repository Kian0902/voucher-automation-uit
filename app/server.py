"""
The desktop app's server: the pages, two kinds of input, the lookups and both agents in one process.

Built from demo/app.py. An XML invoice runs through src/main.py as before. A PNG screenshot of
the ERP list goes through Mistral OCR, the parser and the row agent in src/list_agent.py, and the
agent page shows the rows filling in one by one. One run at a time, in one state every page polls.

The pages are plain HTML files in app/pages/, one per sidebar entry, served by path. They share
the stylesheet and the scripts in app/static/. Every run, finished or failed, appends a record
to output/runs.jsonl for the dashboard (app/api_dashboard.py). The knowledge base page edits
the three report CSVs in place and reloads the lookups (app/api_knowledge.py). The settings
page edits the config file; its changes apply at the next start (app/api_settings.py). The
batch page queues many files and runs them one at a time through the same run (app/api_batch.py).

Paths resolve from APP_ROOT: the repo root in development, the folder next to the executable
when packaged. Configuration comes from config.env in that folder when it exists, else from
.env.local in the repo.
"""

import copy
import json
import os
import re
import sys
import tempfile
import threading
import time
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
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
SETTING_KEYS = ["API_URL", "API_MODEL", "API_KEY", "MISTRAL_API_KEY"]
CONFIG_TEMPLATE = (
    "# Fakturaassistent: innstillinger. Filen ligger ved siden av Fakturaassistent.app.\n"
    "# Fyll inn verdiene mellom anførselstegnene, eller bruk Innstillinger i appen.\n"
    'API_URL=""\n'
    'API_MODEL=""\n'
    'API_KEY=""\n'
    'MISTRAL_API_KEY=""\n'
)
# an address that points nowhere on this Mac: a run with missing settings fails at once and
# can never send invoice data to a server nobody chose
MISSING_URL = "http://127.0.0.1:9/v1"
MISSING_VALUE = "mangler"

CONFIG_FILE = APP_ROOT / "config.env"
DEV_CONFIG_FILE = APP_ROOT / ".env.local"
if not CONFIG_FILE.is_file() and DEV_CONFIG_FILE.is_file():
    CONFIG_FILE = DEV_CONFIG_FILE
if not CONFIG_FILE.is_file():
    # first start next to the app: the staff fill the file in, or use the settings page
    try:
        CONFIG_FILE.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    except OSError:
        pass
load_dotenv(CONFIG_FILE)

# what the app started with, for the settings page; a blank setting gets a placeholder so the
# agents can be imported and the app starts, and the pages say which settings are missing
STARTED_VALUES = {}
SETTINGS_MISSING = []
for setting_key in SETTING_KEYS:
    setting_value = os.environ.get(setting_key, "")
    if setting_value == "":
        STARTED_VALUES[setting_key] = None
        SETTINGS_MISSING.append(setting_key)
        if setting_key == "API_URL":
            os.environ[setting_key] = MISSING_URL
        else:
            os.environ[setting_key] = MISSING_VALUE
    else:
        STARTED_VALUES[setting_key] = setting_value

APP_TITLE = "Fakturaassistent"
REPORTS_DIR = APP_ROOT / "data" / "koststed_tables"
OUTPUT_DIR = APP_ROOT / "output"
# the folder the staff drop the three CSVs into exists from the first start
try:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass

from langchain_core.messages import HumanMessage

import run_log
run_log.LOGS_DIR = APP_ROOT / "logs"

# the app must not grow without end on the staff's PC: old logs and result files go at start,
# the run history (runs.jsonl) behind the dashboard stays (decided 2026-11-10)
LOG_RETENTION_DAYS = 30
OUTPUT_RETENTION_DAYS = 90
RUN_HISTORY_NAME = "runs.jsonl"


def _prune_old_files(folder: Path, days: int, keep: tuple = ()) -> int:
    """
    Delete the files in a folder that are older than a number of days.

    Args:
        folder (Path): The folder; nothing happens when it does not exist.
        days (int): Files modified more than this many days ago are deleted.
        keep (tuple[str, ...]): File names that are never deleted.

    Returns:
        int: How many files were deleted.
    """

    if not folder.is_dir():
        return 0
    cutoff = time.time() - days * 24 * 60 * 60
    deleted = 0
    for path in folder.iterdir():
        if not path.is_file() or path.name in keep:
            continue
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
                deleted = deleted + 1
        except OSError:
            # a file held open by another program is left for the next start
            pass
    return deleted


_prune_old_files(run_log.LOGS_DIR, LOG_RETENTION_DAYS)
_prune_old_files(OUTPUT_DIR, OUTPUT_RETENTION_DAYS, keep=(RUN_HISTORY_NAME,))

import lookup_data
import main as agent_main
import list_agent
from erp_list import COLUMN_KEYS, to_csv, to_json
from lookup_tools import lookup_io, lookup_koststed, lookup_person
from mistral_ocr import build_client, ocr_image
from ocr_parser import parse_ocr_response
from xml_processor import parse_invoice_xml

import api_dashboard
api_dashboard.RECORDS_FILE = OUTPUT_DIR / RUN_HISTORY_NAME
api_dashboard.REPORTS_DIR = REPORTS_DIR

import api_knowledge
api_knowledge.REPORTS_DIR = REPORTS_DIR

import api_settings
api_settings.CONFIG_FILE = CONFIG_FILE
api_settings.FOLDERS = [
    ("Appens mappe", APP_ROOT),
    ("Innstillinger", CONFIG_FILE),
    ("Rapportene", REPORTS_DIR),
    ("Logger", run_log.LOGS_DIR),
    ("Resultater", OUTPUT_DIR),
]

import api_batch


# the app owns the terminal, so the run logs stay quiet; full logs still go to logs/
agent_main.run_log.live_output = False
list_agent.run_log.live_output = False


app = FastAPI(title=APP_TITLE)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.middleware("http")
async def revalidate_static(request: Request, call_next):
    """
    Make the browser revalidate the stylesheet and the scripts on every load.

    Without a Cache-Control header WebKit keeps them for a while on its own judgement, so a new
    version of the app could show its new pages with the old stylesheet.

    Args:
        request (Request): The incoming request.
        call_next: The next handler in the chain.

    Returns:
        Response: The handler's response, with the header added for /static/ files.
    """

    response = await call_next(request)
    if request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response
app.include_router(api_dashboard.router)
app.include_router(api_knowledge.router)
app.include_router(api_settings.router)
app.include_router(api_batch.router)


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
    except Exception as error:
        # the reports are files the staff bring in; whatever is wrong with one is shown, never a crash
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
    "source": None,     # agent | batch: the page the run was started from
    "filename": None,
    "error": None,
    "duration_seconds": None,
    "stages": [],       # [{"key", "title", "description", "status", "verdict"}], status pending | active | done | skipped
    "lookups": [],      # invoice runs: the lookups the resolver asked for
    "problems": [],     # invoice runs: the result rows
    "rows": [],         # list runs: one entry per row of the screenshot
    "rows_done": 0,
    "resultat": None,   # invoice runs: løst | må bekreftes | uløst, decided by the koststed alone
    "saved": [],        # the files written by a list run
    "stop_requested": False,  # the staff pressed stop; the run ends after the current step
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
api_settings.run_is_active = _run_is_active


def _apply_settings(values: dict) -> None:
    """
    Take saved settings into use without a restart.

    The values go into the environment (a blank one gets its placeholder, as at start), the
    model clients are rebuilt from it, and the list of missing settings the pages show is
    refreshed. The OCR client is built per run and needs nothing.

    Args:
        values (dict): key -> value or None, every setting as the config file now has it.
    """

    global SETTINGS_MISSING
    missing = []
    for setting_key in SETTING_KEYS:
        setting_value = values.get(setting_key) or ""
        if setting_value == "":
            missing.append(setting_key)
            if setting_key == "API_URL":
                os.environ[setting_key] = MISSING_URL
            else:
                os.environ[setting_key] = MISSING_VALUE
        else:
            os.environ[setting_key] = setting_value
    SETTINGS_MISSING = missing
    agent_main.configure_clients()
    list_agent.configure_clients()


api_settings.apply_settings = _apply_settings


def _setup_needed() -> bool:
    """
    Whether the setup page must be shown instead of the app: a setting or a report is missing.

    Returns:
        bool: True until all four settings are set and the three reports load.
    """

    return len(SETTINGS_MISSING) > 0 or DATA_ERROR is not None

# Every run gets a number, and the worker thread carries it. A stop ends the run on the page at
# once; the worker is left to finish its current model call and then finds that its run is no
# longer current and writes nothing. One late step of an abandoned worker may still land in the
# shared run log of the next run; the page and the records never see it.
RUN_ID = 0
RUN_STARTED_AT = None
WORKER = threading.local()


def _worker_run_is_current() -> bool:
    """
    Whether the calling worker thread belongs to the run on the page.

    Returns:
        bool: True when the thread's run id is the current one.
    """

    return getattr(WORKER, "run_id", None) == RUN_ID


def _worker_owns_state() -> bool:
    """
    Whether the calling worker thread may still change the run on the page.

    Returns:
        bool: True when its run is current and nobody stopped it.
    """

    return _worker_run_is_current() and STATE["status"] == "running"


def _set_stage(stage_key: str, stage_status: str, verdict=None) -> None:
    """
    Update one stage of the diagram; nothing happens for a worker whose run was stopped.

    Args:
        stage_key (str): The stage's key, for example "identify" or "ocr".
        stage_status (str): "pending", "active", "done", "failed", "stopped" or "skipped".
        verdict (str | None): Verdict line under the stage, kept as is when None.
    """

    if not _worker_owns_state():
        return
    with STATE_LOCK:
        for stage in STATE["stages"]:
            if stage["key"] == stage_key:
                stage["status"] = stage_status
                if verdict is not None:
                    stage["verdict"] = verdict


def _reset_state(filename: str, kind: str, stage_plan: list, source: str) -> int:
    """
    Reset STATE for a new run.

    Args:
        filename (str): Display name of the uploaded file.
        kind (str): "faktura" or "liste".
        stage_plan (list): INVOICE_STAGES or LIST_STAGES.
        source (str): "agent" or "batch", the page the run was started from.

    Returns:
        int: The new run's id, for the worker thread.
    """

    global STATE, RUN_ID, RUN_STARTED_AT
    RUN_ID = RUN_ID + 1
    RUN_STARTED_AT = time.perf_counter()
    stages = []
    for key, title, description in stage_plan:
        stages.append({"key": key, "title": title, "description": description, "status": "pending", "verdict": ""})

    with STATE_LOCK:
        STATE = copy.deepcopy(_IDLE_STATE)
        STATE["status"] = "running"
        STATE["kind"] = kind
        STATE["source"] = source
        STATE["filename"] = filename
        STATE["stages"] = stages
        STATE["stages"][0]["status"] = "done"
        STATE["stages"][0]["verdict"] = filename
        STATE["stages"][1]["status"] = "active"
    return RUN_ID


def _fail_run(message: str) -> None:
    """
    End the run with an error the page shows in the banner.

    Args:
        message (str): The Norwegian error text.
    """

    if not _worker_owns_state():
        return
    with STATE_LOCK:
        STATE["status"] = "error"
        STATE["error"] = message
        _close_open_work("failed", "Feilet")
    _record_run("error", message)


def _close_open_work(stage_status: str, verdict: str) -> None:
    """
    Settle everything still marked active when a run ends early; the caller holds STATE_LOCK.

    The active stage gets the given status and verdict, pending stages are skipped, and a
    lookup without an outcome is marked interrupted, so nothing on the page keeps moving.

    Args:
        stage_status (str): "failed" or "stopped".
        verdict (str): The line under the stage.
    """

    for stage in STATE["stages"]:
        if stage["status"] == "active":
            stage["status"] = stage_status
            stage["verdict"] = verdict
        elif stage["status"] == "pending":
            stage["status"] = "skipped"
    for lookup in STATE["lookups"]:
        if lookup["outcome"] is None:
            lookup["outcome"] = "avbrutt"


def _stop_run() -> None:
    """
    End the run on the page at once because the staff pressed stop.

    Called from the request, not from the worker. What was finished stays on the page and goes
    into the record; the worker thread finds its run stopped at its next check.
    """

    with STATE_LOCK:
        STATE["status"] = "stopped"
        STATE["stop_requested"] = True
        STATE["duration_seconds"] = time.perf_counter() - RUN_STARTED_AT
        _close_open_work("stopped", "Stoppet")
    _record_run("stopped", None)


def _explain_error(error: Exception) -> str:
    """
    The error of a failed run as one plain sentence, with the technical text after it.

    Network failures, a refused key and an unknown address or model are named, since those are
    the cases the staff can fix themselves. Everything else is reported as it is.

    Args:
        error (Exception): What the run raised.

    Returns:
        str: The Norwegian message for the banner and the run record.
    """

    with STATE_LOCK:
        active_stage = None
        for stage in STATE["stages"]:
            if stage["status"] == "active":
                active_stage = stage["key"]
    if active_stage == "ocr":
        service = "Mistral OCR"
    else:
        service = "modellserveren"
    service_first = service[0].upper() + service[1:]

    class_names = []
    for error_class in type(error).__mro__:
        class_names.append(error_class.__name__)
    text = str(error).lower()

    network_classes = ["APIConnectionError", "APITimeoutError", "ConnectError", "ConnectionError",
                       "TimeoutError", "gaierror", "ConnectTimeout", "ReadTimeout"]
    network_words = ["nodename nor servname", "name or service not known", "connection refused",
                     "timed out", "timeout", "network is unreachable", "connection error",
                     "connection reset", "getaddrinfo"]
    is_network = False
    for class_name in network_classes:
        if class_name in class_names:
            is_network = True
    for word in network_words:
        if word in text:
            is_network = True

    if is_network:
        plain = (f"Fikk ikke kontakt med {service}. Sjekk nettverk og VPN, og adressen under "
                 f"Innstillinger, og prøv igjen.")
    elif "AuthenticationError" in class_names or "401" in text or "unauthorized" in text:
        plain = f"Nøkkelen til {service} ble avvist. Sjekk nøkkelen under Innstillinger."
    elif "NotFoundError" in class_names or "404" in text:
        plain = (f"{service_first} svarte at adressen eller modellen ikke finnes. "
                 f"Sjekk adresse og modell under Innstillinger.")
    else:
        return f"Kjøringen feilet: {error}"
    return f"{plain} (Teknisk: {error})"


def _model_failure_reason() -> str:
    """
    Why the invoice agent's last model call gave no usable answer, for the page.

    The call wrapper in src/main.py logs a cut-off generation and a reply that failed the
    schema as a parsing error on the step; everything else is a plain "no conclusion".

    Returns:
        str: A short Norwegian reason.
    """

    last_error = None
    for step in agent_main.run_log.steps:
        if step.kind == "llm" and step.parsing_error is not None:
            last_error = step.parsing_error
    if last_error is None:
        return "Modellen ga ingen konklusjon"
    if "token cap" in last_error:
        return "Modellens svar ble for langt og avbrutt"
    return "Modellens svar kunne ikke tolkes"


def _invoice_result(problems: list) -> str:
    """
    The result of a whole invoice, decided by its koststed alone.

    Kian's rule of 2026-11-10: an invoice with a koststed is løst, whatever else is missing.
    An IO maps to exactly one koststed, so a found IO gives the koststed too; a missing IO
    cannot be derived and must not count against the invoice. The other problems keep their
    own status in the result table.

    Args:
        problems (list[dict]): The result rows of the invoice run.

    Returns:
        str: "løst" when no problem concerns the koststed, or every koststed problem is løst;
             "må bekreftes" when one has a suggestion only; "uløst" when one has none or the
             resolver never reached it.
    """

    koststed_statuses = []
    for problem in problems:
        is_koststed_problem = problem["category"] == LABEL_NAMES[1] or problem["field"] == "buyer_reference"
        if is_koststed_problem:
            koststed_statuses.append(problem["status"])

    if len(koststed_statuses) == 0:
        return "løst"
    for status_text in koststed_statuses:
        if status_text != "løst" and status_text != "må bekreftes":
            return "uløst"
    for status_text in koststed_statuses:
        if status_text == "må bekreftes":
            return "må bekreftes"
    return "løst"


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
        _set_stage("identify", "failed", _model_failure_reason())
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

    if not _worker_owns_state():
        return

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
        reason = _model_failure_reason()
        if resolve_status == "active":
            _set_stage("resolve", "failed", reason)
        # the rows stay on the page as open problems instead of blank cells
        with STATE_LOCK:
            for row in STATE["problems"]:
                if row["status"] == "":
                    row["status"] = "uløst"
                    row["why"] = reason + ". Prøv igjen, eller slå opp manuelt."
    else:
        resolved_count = _fill_resolution_rows(resolutions)
        total_count = len(resolutions)
        _set_stage("resolve", "done", f"{resolved_count} av {total_count} løst")

    final_state = {"labeled_problems": labeled_problems, "resolutions": resolutions}
    agent_main.run_log.finish(final_state)
    agent_main.run_log.save_txt()

    with STATE_LOCK:
        STATE["status"] = "done"
        STATE["resultat"] = _invoice_result(STATE["problems"])
        STATE["duration_seconds"] = agent_main.run_log.duration_seconds()
    _record_run("done", None)


def _run_invoice(xml_bytes: bytes, filename: str, run_id: int) -> None:
    """
    Run the invoice agent on one uploaded XML and update STATE live.

    Runs in a background thread. Every graph update is translated to stage statuses, verdicts
    and lookup lines the page can show. After a stop the thread finishes its current model
    call, finds its run gone and ends without touching the page.

    Args:
        xml_bytes (bytes): The uploaded XML file content.
        filename (str): The original filename, used for display and the sanitized source_file.
        run_id (int): The run this thread belongs to.
    """

    WORKER.run_id = run_id
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
            if not _worker_owns_state():
                break
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
        _fail_run(_explain_error(error))


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
        if not _worker_owns_state():
            break
        with STATE_LOCK:
            STATE["rows"][index]["status"] = "active"

        resolved_row = list_agent.resolve_one_row(row, erp_list["source_file"])
        resolved_list["rows"].append(resolved_row)

        # a row that finished after a stop still counts, as long as the run is the one on the page
        if not _worker_run_is_current():
            break
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


def _save_list_outputs(resolved_list: dict, filename: str) -> list:
    """
    Write the resolved list and the CSV sheet to the output folder.

    The raw OCR response is not kept: nothing reads it, and at 25 KB per screenshot it was the
    largest thing the app wrote (decided 2026-11-10).

    Args:
        resolved_list (dict): The resolved list from _resolve_rows().
        filename (str): The screenshot's filename; its stem names the files.

    Returns:
        list[Path]: The files written.
    """

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = Path(filename).stem

    json_path = OUTPUT_DIR / (stem + ".json")
    json_path.write_text(to_json(resolved_list), encoding="utf-8")

    # utf-8-sig: the byte order mark makes Excel read æøå correctly
    csv_path = OUTPUT_DIR / (stem + ".csv")
    csv_path.write_text(to_csv(resolved_list), encoding="utf-8-sig")

    return [json_path, csv_path]


def _run_list(image_bytes: bytes, filename: str, run_id: int) -> None:
    """
    OCR, parse and resolve one uploaded screenshot and update STATE live.

    Runs in a background thread. After a stop the thread finishes the row in progress, saves
    the rows that were done and ends.

    Args:
        image_bytes (bytes): The uploaded PNG.
        filename (str): The original filename, for display and the output names.
        run_id (int): The run this thread belongs to.
    """

    WORKER.run_id = run_id
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

        if not _worker_owns_state():
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

        if not _worker_owns_state():
            # stopped by the staff: the finished rows are saved for the run still on the page
            if _worker_run_is_current() and len(resolved_list["rows"]) > 0:
                saved_paths = _save_list_outputs(resolved_list, filename)
                with STATE_LOCK:
                    STATE["saved"] = [str(path) for path in saved_paths]
            return

        label_counts = {}
        for resolved_row in resolved_list["rows"]:
            label = resolved_row["resultat"]
            label_counts[label] = label_counts.get(label, 0) + 1
        resolved_count = label_counts.get("løst", 0)
        _set_stage("resolve", "done", f"{resolved_count} av {row_count} løst")

        saved_paths = _save_list_outputs(resolved_list, filename)

        with STATE_LOCK:
            STATE["status"] = "done"
            STATE["duration_seconds"] = time.perf_counter() - started
            STATE["saved"] = [str(path) for path in saved_paths]
        _record_run("done", None)

    except Exception as error:
        _fail_run(_explain_error(error))


# ROUTES

def _page(name: str):
    """
    Serve one of the pages in app/pages/, or the setup page while the app is not set up.

    Args:
        name (str): The file's stem, such as "dashboard".

    Returns:
        HTMLResponse: The page as it stands on disk, so an edit shows on the next load.
        RedirectResponse: To the setup page while a setting or a report is missing.
    """

    if _setup_needed() and name != "setup":
        return RedirectResponse(url="/setup", status_code=303)
    page_path = PAGES_DIR / (name + ".html")
    return HTMLResponse(page_path.read_text(encoding="utf-8"))


@app.get("/setup")
def setup_page():
    """The setup page: the keys and the three reports, before anything else can be used."""

    if not _setup_needed():
        return RedirectResponse(url="/", status_code=303)
    return _page("setup")


@app.get("/")
def dashboard_page() -> HTMLResponse:
    """The dashboard, the first page the staff sees."""

    return _page("dashboard")


@app.get("/agents")
def agents_page() -> HTMLResponse:
    """The page that runs both agents."""

    return _page("agents")


@app.get("/batch")
def batch_page() -> HTMLResponse:
    """The batch page: a queue of files processed one at a time."""

    return _page("batch")


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
    snapshot["settings_missing"] = SETTINGS_MISSING
    snapshot["setup_needed"] = _setup_needed()
    snapshot["run_id"] = RUN_ID
    snapshot["can_quit"] = BROWSER_MODE
    return snapshot


def _stop_if_running() -> bool:
    """
    Stop the run in progress at once, for the stop buttons of the agent page and the batch page.

    Returns:
        bool: True when a run was stopped, False when nothing ran.
    """

    with STATE_LOCK:
        running = STATE["status"] == "running"
    if not running:
        return False
    _stop_run()
    return True


@app.post("/stop")
def stop():
    """
    Stop the running run at once.

    The page settles immediately; the worker thread is abandoned and ends quietly after its
    current step. A new run can start right away.

    Returns:
        A confirmation, or 409 when nothing runs.
    """

    if not _stop_if_running():
        return JSONResponse({"message": "Ingen kjøring pågår"}, status_code=409)
    return {"message": "Kjøringen er stoppet"}


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


# Set by the Windows launcher: the app runs in the staff's browser, has no window of its own
# and therefore needs its own way to quit. The launcher waits on QUIT_REQUESTED.
BROWSER_MODE = False
QUIT_REQUESTED = threading.Event()


@app.post("/quit")
def quit_app():
    """
    End the program, for the "Avslutt appen" button the pages show in browser mode.

    Refused while a file is being processed or waits in the batch queue, so a run is never
    cut off by accident.

    Returns:
        A confirmation, or an error answer.
    """

    if not BROWSER_MODE:
        return JSONResponse({"message": "Lukk vinduet for å avslutte appen"}, status_code=400)
    if _run_is_active() or api_batch.has_unfinished_jobs():
        return JSONResponse(
            {"message": "En kjøring pågår. Stopp den, eller vent til den er ferdig"}, status_code=409,
        )
    QUIT_REQUESTED.set()
    return {"message": "Appen avsluttes"}


# one run starts at a time, whether it comes from the agent page or from the batch queue
RUN_START_LOCK = threading.Lock()


def _start_run(file_bytes: bytes, filename: str, source: str = "agent"):
    """
    Start a run on one file in a background thread: an XML invoice or a PNG screenshot.

    Used by the agent page for a single file and by the batch queue for each of its files, so
    both pass the same checks. The file type is read from the filename's extension.

    Args:
        file_bytes (bytes): The file content.
        filename (str): The original filename.
        source (str): "agent" or "batch". The agent page shows only the runs it started itself.

    Returns:
        tuple[int | None, tuple[str, int] | None]: The run id, or the Norwegian message and the
        HTTP status code when the run could not start.
    """

    suffix = Path(filename).suffix.lower()
    with RUN_START_LOCK:
        with STATE_LOCK:
            already_running = STATE["status"] == "running"
        if already_running:
            return None, ("En kjøring pågår allerede, vent til den er ferdig", 409)
        if DATA_ERROR is not None:
            return None, (DATA_ERROR, 503)
        if len(file_bytes) == 0:
            return None, ("Ingen fil mottatt", 400)

        if suffix == ".xml":
            run_id = _reset_state(filename, "faktura", INVOICE_STAGES, source)
            worker = threading.Thread(target=_run_invoice, args=(file_bytes, filename, run_id), daemon=True)
        elif suffix == ".png":
            run_id = _reset_state(filename, "liste", LIST_STAGES, source)
            worker = threading.Thread(target=_run_list, args=(file_bytes, filename, run_id), daemon=True)
        else:
            return None, ("Filtypen støttes ikke. Slipp en XML-faktura eller et PNG-skjermbilde av listen", 400)
        worker.start()
    return run_id, None


def _read_run_state():
    """
    The run on the page right now, for the batch queue.

    Returns:
        tuple[int, dict]: The run id and a copy of STATE.
    """

    with STATE_LOCK:
        return RUN_ID, copy.deepcopy(STATE)


api_batch.start_run = _start_run
api_batch.read_state = _read_run_state
api_batch.stop_run = _stop_if_running


@app.post("/run")
async def run(request: Request, filename: str = "fil"):
    """
    Start a run on an uploaded file: an XML invoice or a PNG screenshot of the list.

    The run happens in a background thread; the page follows it through /status.

    Args:
        request (Request): The request whose body is the raw file.
        filename (str): The original filename, from the query string.

    Returns:
        A confirmation, or an error message with a fitting status code.
    """

    file_bytes = await request.body()
    run_id, problem = _start_run(file_bytes, filename)
    if run_id is None:
        message, status_code = problem
        return JSONResponse({"message": message}, status_code=status_code)
    return {"message": "startet"}


if __name__ == "__main__":
    # for development in a browser; the desktop window is app/launcher_mac.py
    uvicorn.run(app, host="127.0.0.1", port=8001)

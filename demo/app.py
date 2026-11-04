

import copy
import json
import re
import sys
import tempfile
import threading
from pathlib import Path

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse

# the agent and its helpers live in src/
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root / "src"))

from langchain_core.messages import HumanMessage

import main as agent_main
import lookup_data
from lookup_tools import check_data_loaded, lookup_io, lookup_koststed, lookup_person
from xml_processor import parse_invoice_xml


# the demo owns the terminal, so the run log stays quiet; full logs still go to logs/
agent_main.run_log.live_output = False


app = FastAPI(title="Fakturakontroll demo")


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


# the state the page polls, guarded by STATE_LOCK
_IDLE_STATE = {
    "status": "idle",  # idle | running | done | error
    "filename": None,
    "error": None,
    "duration_seconds": None,
    "stages": {
        "receive": {"status": "pending", "verdict": ""},
        "extract": {"status": "pending", "verdict": ""},
        "identify": {"status": "pending", "verdict": ""},
        "classify": {"status": "pending", "verdict": ""},
        "resolve": {"status": "pending", "verdict": ""},
    },
    "lookups": [],
    "problems": [],
}

DEMO_STATE = copy.deepcopy(_IDLE_STATE)
STATE_LOCK = threading.Lock()


def _set_stage(stage_key: str, stage_status: str, verdict=None) -> None:
    """
    Update one stage of the diagram.

    Args:
        stage_key (str): "receive", "extract", "identify", "classify" or "resolve".
        stage_status (str): "pending", "active", "done" or "skipped".
        verdict (str | None): Verdict line under the stage, kept as is when None.
    """

    with STATE_LOCK:
        DEMO_STATE["stages"][stage_key]["status"] = stage_status
        if verdict is not None:
            DEMO_STATE["stages"][stage_key]["verdict"] = verdict


def _reset_state(filename: str) -> None:
    """
    Reset DEMO_STATE for a new run.

    Args:
        filename (str): Display name of the uploaded file.
    """

    global DEMO_STATE
    with STATE_LOCK:
        DEMO_STATE = copy.deepcopy(_IDLE_STATE)
        DEMO_STATE["status"] = "running"
        DEMO_STATE["filename"] = filename
        DEMO_STATE["stages"]["receive"]["status"] = "done"
        DEMO_STATE["stages"]["receive"]["verdict"] = filename
        DEMO_STATE["stages"]["extract"]["status"] = "active"


def _parse_uploaded_xml(xml_bytes: bytes) -> dict:
    """
    Parse uploaded XML bytes with the ordinary XML processor.

    The processor reads from a path, so the bytes go through a temporary
    file that is deleted right after parsing.

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

    Some example filenames also name the invoice's known problem, and the
    agent must never see that. Same rule as run_all_invoices in src/main.py.

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
        DEMO_STATE["problems"] = rows

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
        rows = DEMO_STATE["problems"]
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
        The resolutions list when the resolver said it is done, otherwise
        previous_resolutions.
    """

    requested_tool = delta["tool"]

    if requested_tool != "none":
        display_name = TOOL_NAMES.get(requested_tool, requested_tool)
        with STATE_LOCK:
            DEMO_STATE["lookups"].append({
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
        for lookup in reversed(DEMO_STATE["lookups"]):
            if lookup["outcome"] is None:
                lookup["outcome"] = outcome
                break


def _clean_suggestion(suggestion: str) -> str:
    """
    Treat suggestions without letters or digits as empty.

    Unresolved problems sometimes get junk like ":" as the suggestion,
    which must not count as a real suggestion.

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
        rows = DEMO_STATE["problems"]
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


def _finalize_run(labeled_problems, resolutions) -> None:
    """
    Close the run: final verdicts, result rows, run log and status.

    Args:
        labeled_problems: The LabeledProblems object, or None.
        resolutions: The final Resolution list, or None.
    """

    # a lookup the loop never executed (the cap stopped it) must not look active
    with STATE_LOCK:
        for lookup in DEMO_STATE["lookups"]:
            if lookup["outcome"] is None:
                lookup["outcome"] = "stoppet, maks antall oppslag nådd"

    if resolutions is None:
        with STATE_LOCK:
            resolve_status = DEMO_STATE["stages"]["resolve"]["status"]
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
        DEMO_STATE["status"] = "done"
        DEMO_STATE["duration_seconds"] = agent_main.run_log.duration_seconds()


def _run_agent(xml_bytes: bytes, filename: str) -> None:
    """
    Run the agent on one uploaded invoice and update DEMO_STATE live.

    Runs in a background thread. Every graph update is translated to stage
    statuses, verdicts and lookup lines the page can show.

    Args:
        xml_bytes (bytes): The uploaded XML file content.
        filename (str): The original filename, used for display and the
            sanitized source_file field.
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

        _finalize_run(labeled_problems, resolutions)

    except Exception as error:
        with STATE_LOCK:
            DEMO_STATE["status"] = "error"
            DEMO_STATE["error"] = f"Kjøringen feilet: {error}"


@app.get("/")
def index() -> HTMLResponse:
    """
    Serve the demo page.

    Returns:
        HTMLResponse: demo/index.html.
    """

    html_path = Path(__file__).resolve().parent / "index.html"
    page = html_path.read_text(encoding="utf-8")
    return HTMLResponse(page)


@app.get("/status")
def status() -> dict:
    """
    Snapshot of the current run for the page to render.

    Returns:
        dict: A copy of DEMO_STATE.
    """

    with STATE_LOCK:
        snapshot = copy.deepcopy(DEMO_STATE)
    return snapshot


def _fetch_ios_for_koststed(code: str) -> dict:
    """
    Lists every IO on one koststed.

    Not in lookup_tools because the agent must not have it; only the manual search uses it.

    Args:
        code (str): The 8-digit koststed.

    Returns:
        dict: status, count and the IO list.
    """

    return lookup_data.lookup_ios_for_koststed(code)


@app.get("/search")
def search(query: str):
    """
    Manual search in the three reports for the support panel.

    Detects what was typed by format: 9 digits starting with 6 is an IO,
    8 digits is a koststed (catalog entry plus its IOs), any other
    all-digit value gets a format hint, and everything else is treated
    as a person name.

    Args:
        query (str): The value typed in the search field.

    Returns:
        dict: kind is "io", "koststed", "person" or "ukjent", with the
        lookup results attached.
    """

    stripped_query = query.strip()

    if stripped_query == "":
        return {"kind": "ukjent", "query": stripped_query, "note": "Skriv inn et IO, et koststed eller et navn"}

    if re.fullmatch(r"6\d{8}", stripped_query) is not None:
        result = lookup_io(stripped_query)
        return {"kind": "io", "query": stripped_query, "result": result}

    if re.fullmatch(r"\d{8}", stripped_query) is not None:
        catalog = lookup_koststed(stripped_query)
        ios = _fetch_ios_for_koststed(stripped_query)
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
async def run(request: Request, filename: str = "faktura.xml"):
    """
    Receive one XML invoice and start the agent in the background.

    The file arrives as the raw request body; the filename comes as a
    query parameter, so no multipart parsing is needed.

    Args:
        request (Request): The request whose body is the XML file.
        filename (str): The original filename of the upload.

    Returns:
        A confirmation, or an error message with a fitting status code.
    """

    with STATE_LOCK:
        already_running = DEMO_STATE["status"] == "running"
    if already_running:
        return JSONResponse(
            {"message": "En kjøring pågår allerede, vent til den er ferdig"},
            status_code=409,
        )

    xml_bytes = await request.body()
    if len(xml_bytes) == 0:
        return JSONResponse({"message": "Ingen fil mottatt"}, status_code=400)

    try:
        check_data_loaded()
    except (FileNotFoundError, ValueError) as error:
        return JSONResponse(
            {"message": f"Oppslagsdataene kunne ikke lastes: {error}"},
            status_code=503,
        )

    _reset_state(filename)
    worker = threading.Thread(target=_run_agent, args=(xml_bytes, filename), daemon=True)
    worker.start()
    return {"message": "startet"}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8001)

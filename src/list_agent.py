"""
Stage 2: find the koststed for one row of the ERP's list of blocked invoices, one row per run.

The row comes from the structure src/ocr_parser.py fills from a screenshot. The agent follows
src/main.py in shape: find every value in the row that can be looked up (a person name, an IO or a
koststed, in any cell), then look them up, one lookup per turn, and give one answer for the row. A
router sends a row with nothing to look up straight to the result; a code scan for the two digit
formats backs the finder up there. A last node without an LLM keeps only the koststeder that a
lookup actually returned and fills the three result fields a resolved row carries after its ERP
columns.
"""

import os
import json
import re
import time
from pathlib import Path
from dotenv import load_dotenv

from typing import TypedDict, Annotated, Sequence
from pydantic import BaseModel, Field
from openai import LengthFinishReasonError

from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from model_client import build_chat_model

from prompts import ROW_LOOKUP_VALUES, ROW_PROBLEM_RESOLUTION
from erp_list import new_resolved_row, to_csv, to_json
from ocr_parser import PARSED_OUTPUT_DIR, print_resolved_tables
from lookup_tools import TOOLS, check_data_loaded
from run_log import RunLog

from utils import save_graph_png


load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")


SYSTEM_PROMPT_FIND = ROW_LOOKUP_VALUES
SYSTEM_PROMPT_RESOLVE = ROW_PROBLEM_RESOLUTION

# the resolver loop stops after this many lookups no matter what
MAX_TOOL_CALLS = 8

# the two digit formats a tool can look up, standing alone in a cell or inside its text
IO_FORMAT = re.compile(r"(?<!\d)6\d{8}(?!\d)")
KOSTSTED_FORMAT = re.compile(r"(?<!\d)[234]\d{7}(?!\d)")

RESOLVED_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "resolved_output"
EVALUATION_RESULTS_DIR = Path(__file__).resolve().parent.parent / "evaluation" / "results"

# the record of the current run, one run per row
run_log = RunLog()



class LookupValue(BaseModel):
    """
    One value in the row that a tool can look up: a person name, an IO or a koststed.
    """

    kind: str = Field(description="'name', 'io' or 'koststed'")
    value: str = Field(description="The value exactly as it stands in the cell; for a name inside free text, the name part only")
    field: str = Field(description="The JSON field name the value stands in")


# SCHEMA: Finder output format
class RowValues(BaseModel):
    """
    The finder's full answer: every value in the row that a tool can look up.
    """
    thinking: str = Field(description="Your private step-by-step reasoning. Think here FIRST, briefly.")
    lookup_values: list[LookupValue] = Field(
        description="Every value in the row that can be looked up, whichever cell it stands in. Empty when there is none."
    )



# For the Resolver
class RowAnswer(BaseModel):
    """
    The resolver's conclusion for the row, kept in the state and the log.

    One answer per row, not one per problem: a koststed found through any value on the row,
    whichever cell it stood in, belongs here.
    """

    koststeder: list[str]
    resolved: bool
    source: str
    why: str


class ResolveStep(BaseModel):
    thinking: str = Field(description="Your private step-by-step reasoning. Think here FIRST, briefly.")
    tool: str = Field(description="Tool to run next: lookup_io, lookup_person, lookup_koststed, or 'none' when done")
    argument: str = Field(description="The single value to look up, e.g. \"612345678\". Empty when tool is 'none'")
    koststeder: list[str] = Field(
        description="Only when tool is 'none': every koststed found for the row, each copied verbatim from a "
                    "tool result. Several when a lookup returned candidates or two lookups disagree. Empty when none"
    )
    resolved: bool = Field(
        description="Only when tool is 'none': true when exactly one koststed is directly supported by a tool result"
    )
    source: str = Field(
        description="Only when tool is 'none': the tool and the value that gave the koststeder. Empty when none"
    )
    why: str = Field(description="Only when tool is 'none': one line on how the conclusion was reached, in Norwegian")
    summary: str = Field(
        description="Only when tool is 'none': one sentence in Norwegian, for the caseworker, on what was "
                    "found and from which lookup, or why nothing was found. Empty while a tool is requested"
    )



# STATE: Whiteboard
class RowState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]  # only the first HumanMessage: the row JSON
    values: RowValues | None  # the finder's lookup values, None when its generation failed
    resolve_messages: list  # resolver tool calls and results
    tool: str
    argument: str
    tool_calls_used: int
    answer: RowAnswer | None  # the resolver's conclusion, set when it says it is done
    summary: str | None  # the resolver's one sentence for the sheet, set with the answer
    funnet_koststed: list  # the result fields, filled by the last node
    resultat: str | None
    begrunnelse: str | None
    grunner: list  # reason codes for a row that is not løst, read by the dashboard



def configure_clients() -> None:
    """
    Build the model client for the chosen server (src/model_client.py) and its two structured wrappers from the environment.

    Called once at import, and again by the app when the settings are saved, so a new key or
    model is used by the next run without a restart. The nodes look the names up when they
    run, so rebinding them here is enough.
    """

    global llm, llm_finder, llm_resolver
    llm = build_chat_model()
    llm_finder = llm.with_structured_output(RowValues, include_raw=True)
    llm_resolver = llm.with_structured_output(ResolveStep, include_raw=True)


configure_clients()



# Same as in main.py. Copied so that importing this module does not build the v1 agent.
# sent once more when a reply was cut off or did not fit the schema; the facts gathered so far stay
RETRY_NUDGE = (
    "Your previous reply was cut off or did not match the required format. Reply again now, "
    "complete and in the required format. Keep 'thinking' to two sentences at most."
)


def _invoke_with_stats(structured_llm, messages: list, step_name: str):
    """
    Invoke a structured LLM, with one retry when the reply is unusable.

    A reply cut at the token cap or one that fails the schema comes back as None from
    _invoke_once. The call is then repeated once with a short nudge appended, since that
    failure is nearly always the model rambling, not missing facts. A second failure is
    reported as None, loud on purpose.

    Args:
        structured_llm: A with_structured_output(..., include_raw=True) wrapper.
        messages (list): The messages to send.
        step_name (str): Log name of the calling step.

    Returns:
        The parsed response object or None when both attempts failed.
    """

    response = _invoke_once(structured_llm, messages, step_name)
    if response is not None:
        return response

    run_log.note("Reply unusable, retrying once with a nudge")
    retry_messages = list(messages) + [HumanMessage(content=RETRY_NUDGE)]
    return _invoke_once(structured_llm, retry_messages, step_name + " (retry)")


def _invoke_once(structured_llm, messages: list, step_name: str):
    """
    Invoke a structured LLM and record the call in the run log.

    Args:
        structured_llm: A with_structured_output(..., include_raw=True) wrapper.
        messages (list): The messages to send.
        step_name (str): Log name of the calling step, e.g. "find".

    Returns:
        The parsed response object or None when parsing fails...
    """


    start_time = time.monotonic()

    # Report llm generations cut at max_completion_tokens as None.
    try:
        result = structured_llm.invoke(messages)
    except LengthFinishReasonError as error:
        elapsed_seconds = time.monotonic() - start_time

        # the cut-off completion still reports its token usage
        input_tokens = None
        output_tokens = None
        usage = getattr(error.completion, "usage", None)
        if usage is not None:
            input_tokens = usage.prompt_tokens
            output_tokens = usage.completion_tokens

        run_log.log_llm_step(step_name, elapsed_seconds, None, input_tokens, output_tokens,
                             None, None, "generation hit the token cap and was cut off")
        return None


    elapsed_seconds = time.monotonic() - start_time

    raw_message = result["raw"]
    usage = getattr(raw_message, "usage_metadata", None)
    if usage is None:
        usage = {}
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")

    metadata = getattr(raw_message, "response_metadata", None)
    if metadata is None:
        metadata = {}
    model_name = metadata.get("model_name")

    # Split the parsed output into the thinking and the real payload
    parsed = result["parsed"]
    thinking_text = None
    output_text = None
    if parsed is not None:
        output_payload = parsed.model_dump()
        if "thinking" in output_payload:
            thinking_text = output_payload["thinking"]
            del output_payload["thinking"]
        output_text = json.dumps(output_payload, indent=2, ensure_ascii=False)

    parsing_error = result["parsing_error"]
    if parsing_error is not None:
        parsing_error = str(parsing_error)

    run_log.log_llm_step(step_name, elapsed_seconds, model_name, input_tokens,
                         output_tokens, output_text, thinking_text, parsing_error)

    return parsed



def _collect_koststed_values(value, koststeder: list) -> None:
    """
    Walk one tool result and append every koststed it carries to the list, once each.

    lookup_io and lookup_person return the koststed under the key "koststed"; lookup_koststed
    confirms the queried code itself, recognised by the "description" it returns when found.

    Args:
        value: A tool result, or any part of one: dict, list or scalar.
        koststeder (list): The list being filled.
    """

    if isinstance(value, dict):
        koststed = value.get("koststed")
        if isinstance(koststed, str) and koststed not in koststeder:
            koststeder.append(koststed)
        if value.get("status") == "found" and "description" in value:
            confirmed_code = value.get("query")
            if isinstance(confirmed_code, str) and confirmed_code not in koststeder:
                koststeder.append(confirmed_code)
        for child in value.values():
            _collect_koststed_values(child, koststeder)
    elif isinstance(value, list):
        for child in value:
            _collect_koststed_values(child, koststeder)


# what a tool result means for a row that ends without a koststed, by tool and status
REASON_CODES = {
    ("lookup_io", "not_found"): "io_ukjent",
    ("lookup_io", "found_unknown_koststed"): "io_uten_koststed",
    ("lookup_person", "none"): "navn_ikke_funnet",
    ("lookup_person", "too_many"): "navn_for_vanlig",
    ("lookup_koststed", "not_in_catalog"): "koststed_ikke_i_katalog",
}


def reasons_in_results(resolve_messages: list) -> list:
    """
    The reason codes the lookups of this run give for a row without a resolved koststed.

    Args:
        resolve_messages (list): The resolver history: [Tool call] AIMessages and ToolMessages.

    Returns:
        list[str]: Codes from REASON_CODES in order of first appearance, no duplicates;
                   "oppslag_feilet" for a tool call that failed.
    """

    reasons = []
    for message in resolve_messages:
        if not isinstance(message, ToolMessage):
            continue
        # a failed tool call is stored as plain text, not JSON
        try:
            result = json.loads(message.content)
        except json.JSONDecodeError:
            code = "oppslag_feilet"
            if code not in reasons:
                reasons.append(code)
            continue
        status = result.get("status")
        if status is None:
            status = result.get("match_type")
        code = REASON_CODES.get((message.tool_call_id, status))
        if code is not None and code not in reasons:
            reasons.append(code)
    return reasons


def koststeder_in_results(resolve_messages: list) -> list:
    """
    Every koststed that a lookup returned during this run.

    Args:
        resolve_messages (list): The resolver history: [Tool call] AIMessages and ToolMessages.

    Returns:
        list[str]: The koststeder in order of first appearance.
    """

    koststeder = []
    for message in resolve_messages:
        if not isinstance(message, ToolMessage):
            continue
        # a failed tool call is stored as plain text, not JSON
        try:
            result = json.loads(message.content)
        except json.JSONDecodeError:
            continue
        _collect_koststed_values(result, koststeder)
    return koststeder



def find_values(state: RowState) -> RowState:
    """
    Ask the LLM for every value in the row that a tool can look up, structured.
    """

    run_log.note("Finding lookup values...")
    messages = [SystemMessage(content=SYSTEM_PROMPT_FIND)] + list(state["messages"])
    response = _invoke_with_stats(llm_finder, messages, "find")
    return {"messages": [], "values": response} # type: ignore



def formats_in_row(row_json: str) -> list:
    """
    Find every IO and koststed format in the row, in any cell, without a model.

    The safety net under the finder: a format the model overlooked still sends the row to
    the resolver. Every cell is scanned, since a reference is often typed into the wrong field;
    a false hit only costs one resolver run that ends unresolved.

    Args:
        row_json (str): The row as the agent received it, one JSON object.

    Returns:
        list[dict]: One dict per hit with kind ('io' or 'koststed'), value and field.
    """

    row = json.loads(row_json)
    hits = []
    for field, cell in row.items():
        if field == "row_number" or cell is None:
            continue
        text = str(cell)
        for io in IO_FORMAT.findall(text):
            hits.append({"kind": "io", "value": io, "field": field})
        for koststed in KOSTSTED_FORMAT.findall(text):
            hits.append({"kind": "koststed", "value": koststed, "field": field})
    return hits


def nothing_to_look_up(state: RowState) -> bool:
    """
    True when neither the finder nor the format scan found anything to look up.

    Args:
        state (RowState): The state after find_values.

    Returns:
        bool: True when the row skips the resolver.
    """

    listed = []
    if state["values"] is not None:
        listed = state["values"].lookup_values
    found_by_code = formats_in_row(state["messages"][0].content)
    return len(listed) == 0 and len(found_by_code) == 0


def router_1(state: RowState):
    """
    Routing after the finder: the resolver when something can be looked up, else the result.
    """

    if nothing_to_look_up(state):
        run_log.note("Nothing on the row can be looked up, skipping the resolver")
        return "exit"
    run_log.note("Entering Resolution Stage")
    return "resolve"



def resolve_row(state: RowState) -> RowState:
    """
    Ask the LLM to find the row's koststed, one tool request per turn.
    """

    run_log.note(f"Resolving... (lookups used: {state['tool_calls_used']})")

    # The row JSON is the first message of the run
    row_json = state["messages"][0].content

    # A failed finder call leaves an empty list; the resolver scans the row itself
    found = state["values"]
    listed_json = "[]"
    if found is not None:
        listed_json = json.dumps([v.model_dump() for v in found.lookup_values], indent=2, ensure_ascii=False)
    formats_json = json.dumps(formats_in_row(row_json), indent=2, ensure_ascii=False)

    task = (f"The row:\n{row_json}\n\n"
            f"Values that can be looked up, listed by the earlier stage:\n{listed_json}\n\n"
            f"IO and koststed formats found in the row by code:\n{formats_json}")

    # The server rejects tool-role messages without a native tool call so stored tool results are relabeled as assistant text
    history = []
    for message in state["resolve_messages"]:
        if isinstance(message, ToolMessage):
            history.append(AIMessage(content=f"[Tool result] {message.content}"))
        else:
            history.append(message)

    messages = [SystemMessage(content=SYSTEM_PROMPT_RESOLVE), HumanMessage(content=task)] + history
    response = _invoke_with_stats(llm_resolver, messages, "resolve")

    # A failed generation ends the loop instead of crashing it
    if response is None:
        return {"messages": [], "tool": "none", "argument": "", "answer": None, "summary": None} # type: ignore

    # The answer and the summary only count when the resolver says it is done
    if response.tool == "none":
        answer = RowAnswer(koststeder=response.koststeder, resolved=response.resolved,
                           source=response.source, why=response.why)
        summary = response.summary
    else:
        answer = None
        summary = None

    return {"messages": [], "tool": response.tool, "argument": response.argument,
            "answer": answer, "summary": summary} # type: ignore



def router_2(state: RowState):
    """
    Routing Function 2 for the agent
    """

    # The resolver either asks for a tool or says it is done
    if state["tool"] == "none":
        run_log.note("Resolution complete")
        return "done"

    if state["tool_calls_used"] >= MAX_TOOL_CALLS:
        run_log.note("Tool call limit reached, stopping the resolution loop")
        return "done"

    # The executor logs the tool call itself, so no note here
    return "tool"



def execute_tool(state: RowState) -> RowState:
    """
    Run the tool the resolver asked for and store the result
    """

    tool_name = state["tool"]
    argument = state["argument"]

    try:
        tool_function = TOOLS[tool_name]
        result = tool_function(argument)
        result_text = json.dumps(result, indent=2, ensure_ascii=False)

        # short status for the terminal
        status_text = result.get("status")
        if status_text is None:
            status_text = result.get("match_type")
        if status_text is None:
            status_text = "ok"

    except Exception as error:
        # Bad tool name or argument names gives feedback instead of crashing...
        result_text = f"Tool call failed: {error}"
        status_text = f"failed ({error})"

    run_log.log_tool_step(tool_name, argument, status_text, result_text)

    # Record the request and its result for the next resolver turn
    request_message = AIMessage(content=f"[Tool call] {tool_name}({argument})")
    result_message = ToolMessage(content=result_text, tool_call_id=tool_name)
    new_history = list(state["resolve_messages"]) + [request_message, result_message]

    return {
        "messages": [],
        "resolve_messages": new_history,
        "tool": "none",
        "argument": "",
        "tool_calls_used": state["tool_calls_used"] + 1
    } # type: ignore



def collect_koststeder(state: RowState) -> RowState:
    """
    Fill the result fields from the answer, keeping only koststeder a lookup returned.

    The rule that every value comes verbatim from a tool result is checked here in code: a
    koststed the resolver lists without any lookup having returned it is dropped and named in
    the begrunnelse. The begrunnelse is the resolver's one-sentence summary; the full reasoning
    stays in the log. No LLM.
    """

    run_log.note("Collecting koststeder...")
    returned = koststeder_in_results(state["resolve_messages"])

    found = []
    dropped = []
    directly_supported = False
    answer = state["answer"]

    if answer is None:
        if nothing_to_look_up(state):
            begrunnelse = "Ingenting på raden kan slås opp: verken navn, IO eller koststed."
            if state["values"] is None:
                begrunnelse = "Finneren ga ingen konklusjon, og raden har verken IO- eller koststedformat."
        else:
            begrunnelse = "Modellen ga ingen konklusjon."
    else:
        for koststed in answer.koststeder:
            if koststed in returned:
                if koststed not in found:
                    found.append(koststed)
            else:
                dropped.append(koststed)
        directly_supported = answer.resolved
        begrunnelse = state["summary"]
        if begrunnelse is None or begrunnelse == "":
            begrunnelse = "Modellen ga ingen oppsummering."

    if len(dropped) > 0:
        begrunnelse = begrunnelse + f" Forkastet, ingen oppslag ga dem: {', '.join(dropped)}."

    # løst: one koststed directly supported; må bekreftes: one to confirm; flere funnet; uløst
    if len(found) == 0:
        resultat = "uløst"
    elif len(found) > 1:
        resultat = "flere funnet"
    elif directly_supported:
        resultat = "løst"
    else:
        resultat = "må bekreftes"

    # the reason codes for the dashboard: what the lookups said, and why there was no answer
    grunner = []
    if resultat != "løst":
        grunner = reasons_in_results(state["resolve_messages"])
        if answer is None:
            if nothing_to_look_up(state):
                grunner.insert(0, "ingen_verdi")
            else:
                grunner.insert(0, "ingen_konklusjon")
        if resultat == "uløst" and len(grunner) == 0:
            grunner.append("annet")

    return {
        "messages": [],
        "funnet_koststed": found,
        "resultat": resultat,
        "begrunnelse": begrunnelse,
        "grunner": grunner
    } # type: ignore




graph = StateGraph(RowState)
graph.add_node("find_values", find_values)
graph.add_node("resolve_row", resolve_row)
graph.add_node("execute_tool", execute_tool)
graph.add_node("collect_koststeder", collect_koststeder)

graph.add_conditional_edges(
    source="find_values",
    path=router_1,
    path_map={
        "resolve": "resolve_row",
        "exit": "collect_koststeder"
    }
)

graph.add_conditional_edges(
    source="resolve_row",
    path=router_2,
    path_map={
        "tool": "execute_tool",
        "done": "collect_koststeder"
    }
)

graph.add_edge("execute_tool", "resolve_row")
graph.add_edge("collect_koststeder", END)

graph.set_entry_point("find_values")
row_agent = graph.compile()


# save_graph_png(row_agent)



def row_for_agent(row: dict) -> dict:
    """
    The row without its confidence dict: the agent reads values, not OCR scores.

    Args:
        row (dict): A row from the parsed structure.

    Returns:
        dict: The row number and the eleven columns.
    """

    agent_row = {}
    for key, value in row.items():
        if key == "confidence":
            continue
        agent_row[key] = value
    return agent_row


def resolve_one_row(row: dict, source_file: str) -> dict:
    """
    Run the agent on one row and return the row with the result fields filled.

    Args:
        row (dict): A row from the parsed structure.
        source_file (str): File name of the screenshot, for the run log.

    Returns:
        dict: The row from erp_list.new_resolved_row() with funnet_koststed, resultat,
              begrunnelse, tid_brukt (whole seconds), tokens_brukt (prompt plus generated,
              all model calls of the row), oppslag_brukt and grunner filled in.
    """

    run_log.start(f"{source_file} rad {row['row_number']}")
    row_json = json.dumps(row_for_agent(row), indent=2, ensure_ascii=False)

    initial_state: RowState = {
        "messages": [HumanMessage(content=row_json)],
        "values": None,
        "resolve_messages": [],
        "tool": "none",
        "argument": "",
        "tool_calls_used": 0,
        "answer": None,
        "summary": None,
        "funnet_koststed": [],
        "resultat": None,
        "begrunnelse": None,
        "grunner": []
    }

    result = row_agent.invoke(initial_state)
    run_log.finish(result)
    run_log.save_txt()              # write the full log to logs/

    resolved_row = new_resolved_row(row)
    resolved_row["funnet_koststed"] = result["funnet_koststed"]
    resolved_row["resultat"] = result["resultat"]
    resolved_row["begrunnelse"] = result["begrunnelse"]
    resolved_row["tid_brukt"] = round(run_log.duration_seconds())
    resolved_row["tokens_brukt"] = run_log.total_input_tokens() + run_log.total_output_tokens()
    resolved_row["oppslag_brukt"] = run_log.lookups_used
    resolved_row["grunner"] = result["grunner"]
    return resolved_row


def resolve_list(erp_list: dict) -> dict:
    """
    Run the agent on every row of one screenshot's structure, in order.

    Args:
        erp_list (dict): The structure from src/ocr_parser.py, read from data/parsed_output/.

    Returns:
        dict: {"source_file", "category", "rows"} with one resolved row per input row.
    """

    # one line per row in the terminal; the full logs still go to logs/
    run_log.live_output = False

    resolved_list = {
        "source_file": erp_list["source_file"],
        "category": erp_list["category"],
        "rows": [],
    }
    for row in erp_list["rows"]:
        resolved_row = resolve_one_row(row, erp_list["source_file"])
        resolved_list["rows"].append(resolved_row)
        print(f"rad {row['row_number']}: {resolved_row['resultat']} "
              f"({run_log.lookups_used} oppslag, {run_log.duration_seconds():.1f} s)")
    return resolved_list


def save_resolved(resolved_list: dict, screenshot_name: str) -> tuple[Path, Path]:
    """
    Write the resolved list as JSON and as a CSV sheet, one pair of files per screenshot.

    Args:
        resolved_list (dict): The structure from resolve_list().
        screenshot_name (str): File name of the screenshot; its stem names both output files.

    Returns:
        tuple[Path, Path]: The JSON file and the CSV file written.
    """

    RESOLVED_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    stem = Path(screenshot_name).stem
    json_path = RESOLVED_OUTPUT_DIR / (stem + ".json")
    json_path.write_text(to_json(resolved_list), encoding="utf-8")
    # utf-8-sig: the byte order mark makes Excel read æøå correctly
    csv_path = RESOLVED_OUTPUT_DIR / (stem + ".csv")
    csv_path.write_text(to_csv(resolved_list), encoding="utf-8-sig")
    return json_path, csv_path



def find_parsed_list(screenshot_name: str) -> Path:
    """
    Find the parsed structure of one screenshot: data/parsed_output/ first, then the evaluation results.

    Args:
        screenshot_name (str): File name of the screenshot.

    Returns:
        Path: data/parsed_output/<stem>.json when it exists, else evaluation/results/<stem>/erp_list.json.

    Raises:
        FileNotFoundError: When neither file exists.
    """

    stem = Path(screenshot_name).stem
    parsed_path = PARSED_OUTPUT_DIR / (stem + ".json")
    if parsed_path.is_file():
        return parsed_path

    # the evaluation parsed every screenshot once; reuse that rather than pay for another OCR call
    evaluation_path = EVALUATION_RESULTS_DIR / stem / "erp_list.json"
    if evaluation_path.is_file():
        return evaluation_path

    raise FileNotFoundError(
        f"no parsed list for {screenshot_name}: neither {parsed_path} nor {evaluation_path} exists; "
        "run src/screenshot_processor.py on that screenshot first"
    )



if __name__ == "__main__":
    # Change this name to resolve another screenshot. One screenshot per run by decision.
    screenshot_name = "Skjermbilde 2026-10-13 081059.png"

    json_path = find_parsed_list(screenshot_name)
    print(f"parsed list: {json_path}")

    # loads the three reports on the first run; a missing or changed CSV fails here, not mid-run
    check_data_loaded()

    erp_list = json.loads(json_path.read_text(encoding="utf-8"))
    resolved_list = resolve_list(erp_list)
    output_json, output_csv = save_resolved(resolved_list, screenshot_name)

    print()
    #print_resolved_tables(resolved_list)
    print(f"saved to: {output_json}")
    print(f"saved to: {output_csv}")

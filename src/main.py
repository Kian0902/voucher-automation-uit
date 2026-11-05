

import os
import re
import json
import time
from pathlib import Path
from dotenv import load_dotenv

from typing import TypedDict, Annotated, Sequence
from pydantic import BaseModel, Field, SecretStr
from openai import LengthFinishReasonError

from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI

from prompts import INVOICE_PROBLEM_IDENTIFICATION, INVOICE_PROBLEM_CLASSIFICATION, INVOICE_PROBLEM_RESOLUTION
from xml_processor import parse_invoice_xml
from lookup_tools import TOOLS, check_data_loaded
from run_log import RunLog

from utils import save_graph_png


load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")


SYSTEM_PROMPT_IDENTIFY = INVOICE_PROBLEM_IDENTIFICATION
SYSTEM_PROMPT_CLASSIFY = INVOICE_PROBLEM_CLASSIFICATION
SYSTEM_PROMPT_RESOLVE = INVOICE_PROBLEM_RESOLUTION

# the resolver loop stops after this many lookups no matter what
MAX_TOOL_CALLS = 8

# the record of the current run
run_log = RunLog()



class Problem(BaseModel):
    """
    One identified problem with the invoice.
    """

    problem: str = Field(description="One-line statement of the problem, in Norwegian")
    field: str = Field(description="The JSON field name the problem concerns")
    value: str = Field(description="The extracted value, or 'missing'")
    why: str = Field(description="One line on why this is a problem, in Norwegian")


# SCHEMA: Agent output format
class InvoiceProblems(BaseModel):
    """
    The agent's full answer: every problem identified in one invoice.
    """
    thinking: str = Field(description="Your private step-by-step reasoning. Think here FIRST, briefly.")
    problems: list[Problem] = Field(
        description="All identified problems. Empty when the invoice has none."
    )



# For the Classifier
class LabeledProblem(Problem):
    label: int = Field(description="The label number for the problem")

class LabeledProblems(BaseModel):
    thinking: str = Field(description="Your private step-by-step reasoning. Think here FIRST, briefly.")
    problems: list[LabeledProblem] = Field(
        description="Every problem received, now labeled. Empty when the invoice has none."
    )



# For the Resolver
class Resolution(BaseModel):
    """
    One problem with the conclusion the resolver reached for it.
    """

    problem: str = Field(description="The problem this resolution answers, as received")
    resolved: bool = Field(description="True only when a tool result directly supports the suggestion")
    suggestion: str = Field(description="The suggested correction, empty when unresolved")
    source: str = Field(description="The tool result the suggestion comes from, empty when unresolved")
    why: str = Field(description="One line on how the conclusion was reached, in Norwegian")


class ResolveStep(BaseModel):
    thinking: str = Field(description="Your private step-by-step reasoning. Think here FIRST, briefly.")
    tool: str = Field(description="Tool to run next: lookup_io, lookup_person, lookup_koststed, or 'none' when done")
    argument: str = Field(description="The single value to look up, e.g. \"612345678\". Empty when tool is 'none'")
    resolutions: list[Resolution] = Field(
        description="Only when tool is 'none': one resolution per problem received, same order."
    )



# STATE: Whiteboard
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    problems: InvoiceProblems | None
    labeled_problems: LabeledProblems | None
    resolve_messages: list  #  resolver tool calls and results
    tool: str
    argument: str
    tool_calls_used: int
    resolutions: list[Resolution] | None



llm = ChatOpenAI(
    model=os.environ["API_MODEL"],
    base_url=os.environ["API_URL"],
    api_key=SecretStr(os.environ["API_KEY"]),
    temperature=0.1,         # 0.0 spirals, and frequency_penalty corrupted verbatim values (measured) - do not reintroduce either
    timeout=120,
    max_completion_tokens=7000,
    max_retries=2,
)

llm_identifier = llm.with_structured_output(InvoiceProblems, include_raw=True)
llm_classifier = llm.with_structured_output(LabeledProblems, include_raw=True)
llm_resolver = llm.with_structured_output(ResolveStep, include_raw=True)



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
        step_name (str): Log name of the calling step, e.g. "identify".

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




def identify_problems(state: AgentState) -> AgentState:
    """
    Ask the LLM to identify the invoice's problems, structured.
    """

    run_log.note("Identifying problems...")
    messages = [SystemMessage(content=SYSTEM_PROMPT_IDENTIFY)] + list(state["messages"])
    response = _invoke_with_stats(llm_identifier, messages, "identify")
    return {"messages": [], "problems": response, "labeled_problems": None} # type: ignore



def router_1(state: AgentState):
    """
    Routing Function
    """

    # If no problems were identified
    if state["problems"] is None or len(state["problems"].problems) == 0:
        run_log.note("Could not identify any problems...")
        return "exit"
    else:
        run_log.note("Entering Classification Stage")
        return "continue"


def classify_problems(state: AgentState) -> AgentState:
    """
    Ask the LLM to classify the identified invoice's problems.
    """

    run_log.note("Classifying problems...")
    identified = state["problems"]

    # If not problems were identified
    if identified is None or len(identified.problems) == 0:
        return {"messages": [], "labeled_problems": None} # type: ignore

    # Exclude the identifier thinking
    problems_json = json.dumps([p.model_dump() for p in identified.problems], indent=2, ensure_ascii=False)
    messages = [
        SystemMessage(content=SYSTEM_PROMPT_CLASSIFY),
        HumanMessage(content=problems_json)
    ]
    response = _invoke_with_stats(llm_classifier, messages, "classify")
    return {"messages": [], "labeled_problems": response} # type: ignore



def resolve_problems(state: AgentState) -> AgentState:
    """
    Ask the LLM to resolve the classified problems, one tool request per turn.
    """

    run_log.note(f"Resolving problems... (lookups used: {state['tool_calls_used']})")
    labeled = state["labeled_problems"]

    # Nothing to resolve when classification produced nothing
    if labeled is None:
        return {"messages": [], "tool": "none", "argument": "", "resolutions": None} # type: ignore

    problems_json = json.dumps([p.model_dump() for p in labeled.problems], indent=2, ensure_ascii=False)

    # The invoice JSON is the first message of the run
    invoice_json = state["messages"][0].content
    task = f"The invoice:\n{invoice_json}\n\nThe classified problems:\n{problems_json}"

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
        return {"messages": [], "tool": "none", "argument": "", "resolutions": None} # type: ignore

    # The resolutions only count when the resolver says it is done
    if response.tool == "none":
        resolutions = response.resolutions
    else:
        resolutions = None

    return {"messages": [], "tool": response.tool, "argument": response.argument, "resolutions": resolutions} # type: ignore



def router_2(state: AgentState):
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



def execute_tool(state: AgentState) -> AgentState:
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




graph = StateGraph(AgentState)
graph.add_node("identify_problems", identify_problems)
graph.add_node("classify_problems", classify_problems)
graph.add_node("resolve_problems", resolve_problems)
graph.add_node("execute_tool", execute_tool)

graph.add_conditional_edges(
    source="identify_problems",
    path=router_1,
    path_map={
        "continue": "classify_problems",
        "exit": END
    }
)

graph.add_edge("classify_problems", "resolve_problems")

graph.add_conditional_edges(
    source="resolve_problems",
    path=router_2,
    path_map={
        "tool": "execute_tool",
        "done": END
    }
)

graph.add_edge("execute_tool", "resolve_problems")

graph.set_entry_point("identify_problems")
agent = graph.compile()


# save_graph_png(agent)



def run_system():

    # loads the three reports on the first run; a missing or changed CSV fails here, not mid-run
    check_data_loaded()

    data_dir = Path(__file__).resolve().parent.parent / "data" / "example_invoices"
    name = "Faktura 449550.xml"

    # for name in data_dir.rglob("*.xml"):

    run_log.start(name)
    invoice = parse_invoice_xml(data_dir / name)

    # # some example filenames also name the invoice's known problem, and
    # # the agent must never see that - keep only the type and the number
    # raw_source = invoice["source_file"]
    # source_match = re.match(r"^(Faktura|Kreditnota)\s+([0-9-]+)", raw_source)
    # if source_match is not None:
    #     invoice["source_file"] = source_match.group(1) + " " + source_match.group(2)
    # else:
    #     del invoice["source_file"]

    invoice_json = json.dumps(invoice, indent=2, ensure_ascii=False)

    # print(invoice_json)

    initial_state: AgentState = {
        "messages": [HumanMessage(content=invoice_json)],
        "problems": None,
        "labeled_problems": None,
        "resolve_messages": [],
        "tool": "none",
        "argument": "",
        "tool_calls_used": 0,
        "resolutions": None
    }

    result = agent.invoke(initial_state)
    run_log.finish(result)

    print()
    run_log.print_summary()

    # deeper views when needed:
    # run_log.print_step("resolve")   # one step in detail: "identify", "classify", "resolve", "tool", "note"
    # run_log.print_full()            # everything, including the thinking
    run_log.save_txt()              # write the full log to logs/


def run_all_invoices():
    """
    Run the agent on every example XML invoice and print a scoreboard.
    """

    # loads the three reports on the first run; a missing or changed CSV fails here, not mid-run
    check_data_loaded()

    # only the final scoreboard in the terminal; the full logs still go to logs/
    run_log.live_output = False

    data_dir = Path(__file__).resolve().parent.parent / "data" / "example_invoices"
    xml_files = sorted(data_dir.glob("*.xml"))
    scores = []
    print(f"{len(xml_files)} invoices, one log per run in logs/, scoreboard at the end")

    for index, xml_file in enumerate(xml_files, start=1):
        invoice = parse_invoice_xml(xml_file)

        # some example filenames also name the invoice's known problem, and
        # the agent must never see that - keep only the type and the number
        raw_source = invoice["source_file"]
        source_match = re.match(r"^(Faktura|Kreditnota)\s+([0-9-]+)", raw_source)
        if source_match is not None:
            invoice["source_file"] = source_match.group(1) + " " + source_match.group(2)
        else:
            del invoice["source_file"]

        invoice_json = json.dumps(invoice, indent=2, ensure_ascii=False)

        run_log.start(xml_file.name)
        initial_state: AgentState = {
            "messages": [HumanMessage(content=invoice_json)],
            "problems": None,
            "labeled_problems": None,
            "resolve_messages": [],
            "tool": "none",
            "argument": "",
            "tool_calls_used": 0,
            "resolutions": None
        }

        result = agent.invoke(initial_state)
        run_log.finish(result)
        run_log.save_txt()

        # count how many of the invoice's problems got a resolved resolution
        resolutions = result["resolutions"]
        if resolutions is None:
            resolutions = []
        resolved_count = 0
        for resolution in resolutions:
            if resolution.resolved:
                resolved_count = resolved_count + 1

        scores.append((xml_file.stem, resolved_count, len(resolutions)))
        print(f"{index}/{len(xml_files)} {xml_file.stem}: {resolved_count}/{len(resolutions)} resolved, {run_log.duration_seconds():.1f} s")

    # final scoreboard over all invoices
    total_resolved = 0
    total_problems = 0
    for invoice_name, resolved_count, problem_count in scores:
        total_resolved = total_resolved + resolved_count
        total_problems = total_problems + problem_count

    print(f"\n===== SCOREBOARD: {total_resolved}/{total_problems} problems resolved =====")
    for invoice_name, resolved_count, problem_count in scores:
        print(f"    {invoice_name} ({resolved_count}/{problem_count})")


if __name__ == "__main__":
    # run_system()
    run_all_invoices()

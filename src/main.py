

import os
import re
import json
import time
from pathlib import Path
from dotenv import load_dotenv

from typing import TypedDict, Annotated, Sequence
from pydantic import BaseModel, Field, SecretStr

from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage, AIMessage, ToolMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI

from prompts import INVOICE_PROBLEM_IDENTIFICATION, INVOICE_PROBLEM_CLASSIFICATION, INVOICE_PROBLEM_RESOLUTION
from xml_processor import parse_invoice_xml
from lookup_tools import TOOLS

from utils import save_graph_png, print_invoice_problems


load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")


SYSTEM_PROMPT_IDENTIFY = INVOICE_PROBLEM_IDENTIFICATION
SYSTEM_PROMPT_CLASSIFY = INVOICE_PROBLEM_CLASSIFICATION
SYSTEM_PROMPT_RESOLVE = INVOICE_PROBLEM_RESOLUTION

# the resolver loop stops after this many lookups no matter what
MAX_TOOL_CALLS = 8



class Problem(BaseModel):
    """
    One identified problem with the invoice.
    """

    problem: str = Field(description="One-line statement of the problem")
    field: str = Field(description="The JSON field name the problem concerns")
    value: str = Field(description="The extracted value, or 'missing'")
    why: str = Field(description="One line on why this is a problem")


# SCHEMA: Agent output format
class InvoiceProblems(BaseModel):
    """
    The agent's full answer: every problem identified in one invoice.
    """
    thinking: str = Field(description="Your private step-by-step reasoning. Think here FIRST.")
    problems: list[Problem] = Field(
        description="All identified problems. Empty when the invoice has none."
    )



# For the Classifier
class LabeledProblem(Problem):
    label: int = Field(description="The label number for the problem")

class LabeledProblems(BaseModel):
    thinking: str = Field(description="Your private step-by-step reasoning. Think here FIRST.")
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
    why: str = Field(description="One line on how the conclusion was reached")


class ResolveStep(BaseModel):
    thinking: str = Field(description="Your private step-by-step reasoning. Think here FIRST.")
    tool: str = Field(description="Tool to run next: lookup_io, lookup_person, lookup_koststed, or 'none' when done")
    args: dict = Field(description="The tool's arguments with exact names, e.g. {\"io\": \"612345678\"}. Empty when tool is 'none'")
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
    args: dict
    tool_calls_used: int
    resolutions: list[Resolution] | None



llm = ChatOpenAI(
    model=os.environ["API_MODEL"],
    base_url=os.environ["API_URL"],
    api_key=SecretStr(os.environ["API_KEY"]),
    temperature=0.1,
    timeout=120,
    max_completion_tokens=3000,
    max_retries=2,
)

llm_identifier = llm.with_structured_output(InvoiceProblems, include_raw=True)
llm_classifier = llm.with_structured_output(LabeledProblems, include_raw=True)
llm_resolver = llm.with_structured_output(ResolveStep, include_raw=True)



def _invoke_with_stats(structured_llm, messages: list, step_name: str):
    """
    Invoke a structured LLM and print elapsed time and token usage.

    Args:
        structured_llm: A with_structured_output(..., include_raw=True) wrapper.
        messages (list): The messages to send.
        step_name (str): Short name printed with the stats, e.g. "identify".

    Returns:
        The parsed response object, or None when parsing failed.
    """


    start_time = time.monotonic()
    result = structured_llm.invoke(messages)
    elapsed_seconds = time.monotonic() - start_time

    raw_message = result["raw"]
    usage = getattr(raw_message, "usage_metadata", None)
    if usage is None:
        usage = {}
    input_tokens = usage.get("input_tokens")
    output_tokens = usage.get("output_tokens")

    print(f"[{step_name}] {elapsed_seconds:.1f}s, tokens in/out: {input_tokens}/{output_tokens}")

    if result["parsing_error"] is not None:
        print(f"[{step_name}] parsing error: {result['parsing_error']}")

    return result["parsed"]




def identify_problems(state: AgentState) -> AgentState:
    """
    Ask the LLM to identify the invoice's problems, structured.
    """

    print("Identifying problems...")
    messages = [SystemMessage(content=SYSTEM_PROMPT_IDENTIFY)] + list(state["messages"])
    response = _invoke_with_stats(llm_identifier, messages, "identify")
    return {"messages": [], "problems": response, "labeled_problems": None} # type: ignore



def router_1(state: AgentState):
    """
    Routing Function
    """

    # If no problems were identified
    if state["problems"] is None or len(state["problems"].problems) == 0:
        print("Could not identify any problems...")
        return "exit"
    else:
        print("Entering Classification Stage")
        return "continue"


def classify_problems(state: AgentState) -> AgentState:
    """
    Ask the LLM to classify the identified invoice's problems.
    """

    print("Classifying problems...")
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

    print(f"Resolving problems... (lookups used: {state['tool_calls_used']})")
    labeled = state["labeled_problems"]

    # Nothing to resolve when classification produced nothing
    if labeled is None:
        return {"messages": [], "tool": "none", "args": {}, "resolutions": None} # type: ignore

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
        return {"messages": [], "tool": "none", "args": {}, "resolutions": None} # type: ignore

    # The resolutions only count when the resolver says it is done
    if response.tool == "none":
        resolutions = response.resolutions
    else:
        resolutions = None

    return {"messages": [], "tool": response.tool, "args": response.args, "resolutions": resolutions} # type: ignore



def router_2(state: AgentState):
    """
    Routing Function 2 for the agent
    """

    # The resolver either asks for a tool or says it is done
    if state["tool"] == "none":
        print("Resolution complete")
        return "done"

    if state["tool_calls_used"] >= MAX_TOOL_CALLS:
        print("Tool call limit reached, stopping the resolution loop")
        return "done"

    print(f"Running tool: {state['tool']}")
    return "tool"



def execute_tool(state: AgentState) -> AgentState:
    """
    Run the tool the resolver asked for and store the result
    """

    tool_name = state["tool"]
    args_text = json.dumps(state["args"], ensure_ascii=False)

    try:
        tool_function = TOOLS[tool_name]
        result = tool_function(**state["args"])
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
        status_text = "failed"

    print(f"Tool {tool_name} answered: {status_text}")

    # Record the request and its result for the next resolver turn
    request_message = AIMessage(content=f"[Tool call] {tool_name}({args_text})")
    result_message = ToolMessage(content=result_text, tool_call_id=tool_name)
    new_history = list(state["resolve_messages"]) + [request_message, result_message]

    return {
        "messages": [],
        "resolve_messages": new_history,
        "tool": "none",
        "args": {},
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
    data_dir = Path(__file__).resolve().parent.parent / "data" / "example_invoices"
    name = "Faktura 1473715-3799.xml"

    # for name in data_dir.rglob("*.xml"):

    print("\n ===== START =====")
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
        "args": {},
        "tool_calls_used": 0,
        "resolutions": None
    }

    result = agent.invoke(initial_state)

    print_invoice_problems(result=result)

    # the resolver conclusions (one block per problem)
    print(f"\nLookups used: {result['tool_calls_used']}")
    resolutions = result["resolutions"]
    if resolutions is None:
        print("No resolutions produced.")
    else:
        for resolution in resolutions:
            if resolution.resolved:
                status = "resolved"
            else:
                status = "unresolved"
            print(f"- [{status}] {resolution.problem}")
            print(f"    - Suggestion: {resolution.suggestion}")
            print(f"    - Source:     {resolution.source}")
            print(f"    - Why:        {resolution.why}")
            print("\n")

    print("\n ===== END =====")
    print("\n\n\n\n")


if __name__ == "__main__":
    run_system()

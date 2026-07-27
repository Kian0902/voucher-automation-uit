import os
import json
from pathlib import Path
from dotenv import load_dotenv

from typing import TypedDict, Annotated, Sequence
from pydantic import BaseModel, Field, SecretStr

from langchain_core.messages import BaseMessage, SystemMessage, HumanMessage
from langgraph.graph import StateGraph, END
from langgraph.graph.message import add_messages
from langchain_openai import ChatOpenAI

from prompts import INVOICE_PROBLEM_IDENTIFICATION, INVOICE_PROBLEM_CLASSIFICATION
from xml_processor import parse_invoice_xml

from utils import save_graph_png, print_invoice_problems


load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")


SYSTEM_PROMPT_IDENTIFY = INVOICE_PROBLEM_IDENTIFICATION
SYSTEM_PROMPT_CLASSIFY = INVOICE_PROBLEM_CLASSIFICATION



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


# STATE: Whiteboard
class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]
    problems: InvoiceProblems | None
    labeled_problems: LabeledProblems | None



llm = ChatOpenAI(
    model=os.environ["API_MODEL"],
    base_url=os.environ["API_URL"],
    api_key=SecretStr(os.environ["API_KEY"]),
    temperature=0.1,
    timeout=120,
    #max_completion_tokens=3000,
)

llm_identifier = llm.with_structured_output(InvoiceProblems)
llm_classifier = llm.with_structured_output(LabeledProblems)




def identify_problems(state: AgentState) -> AgentState:
    """
    Ask the LLM to identify the invoice's problems, structured.
    """

    messages = [SystemMessage(content=SYSTEM_PROMPT_IDENTIFY)] + list(state["messages"])
    response: InvoiceProblems = llm_identifier.invoke(messages)  # type: ignore
    return {"messages": [], "problems": response, "labeled_problems": None}



def router_1(state: AgentState):
    """
    Routing Function
    """

    # If not problems were identified
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
    response: LabeledProblems = llm_classifier.invoke(messages) # type: ignore
    return {"messages": [], "labeled_problems": response} # type: ignore




graph = StateGraph(AgentState)
graph.add_node("identify_problems", identify_problems)
graph.add_node("classify_problems", classify_problems)

# graph.add_edge("identify_problems", "classify_problems")

graph.add_conditional_edges(
    source="identify_problems",
    path=router_1,
    path_map={
        "continue": "classify_problems",
        "exit": END
    }
)

graph.add_edge("classify_problems", END)

graph.set_entry_point("identify_problems")
agent = graph.compile()


# save_graph_png(agent)



def run_system():
    data_dir = Path(__file__).resolve().parent.parent / "data" / "example_invoices"
    name = "Faktura 8111457.xml"

    # for name in data_dir.rglob("*.xml"):

    print("\n ===== START =====")
    #data_dir = Path(__file__).resolve().parent.parent / "data" / "example_invoices"
    invoice = parse_invoice_xml(data_dir / name)
    invoice_json = json.dumps(invoice, indent=2, ensure_ascii=False)
    

    initial_state: AgentState = {
        "messages": [HumanMessage(content=invoice_json)],
        "problems": None,
        "labeled_problems": None
    }

    result = agent.invoke(initial_state)

    print_invoice_problems(result=result)

    print("\n ===== END =====")
    print("\n\n\n\n")


if __name__ == "__main__":
    run_system()

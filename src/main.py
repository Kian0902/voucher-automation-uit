
import json
import os
from pathlib import Path

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, START, END, MessagesState
from pydantic import BaseModel, Field, SecretStr

from xml_processor import parse_invoice_xml


load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")


SYSTEM_PROMPT = """\
# Role

You assist the finance department at UiT with incoming invoices that
failed automated booking.

# Task

You receive the extracted data of one invoice as JSON. Identify and
articulate the problem(s) with the invoice so a caseworker instantly
understands what is wrong.

# What to look at

Only the problems that actually stop an invoice in UiT's ERP (Unit4),
most important first:

1. Supplier identification: supplier organisation number is missing or
   is the placeholder "999999999", and/or the bank account number is
   missing. Unit4 matches suppliers on organisation number AND account.
2. Buyer reference ("Deres referanse" / buyer_reference): missing, a
   placeholder like "N/A" or ".", or a person name instead of a valid
   reference code.
3. Invoice number missing.
4. KID missing.

# What to ignore

Everything that does not stop an invoice, including:

- Dates of any kind (issue date, due date, delivery date, periods)
- Amounts and totals. A payable amount of 0.00 is legitimate (prepaid
  or fully discounted) - never report it
- Tax details, delivery information, attachments, contact details
- Anything else not listed under "What to look at"

# Rules

- Do not suggest fixes
- Do not invent values that are not in the data
- Duplicate detection and registry lookups are impossible from one
  invoice alone - never claim an invoice is a duplicate or that a
  supplier is unregistered
"""


class Problem(BaseModel):
    """
    One identified problem with the invoice.
    """

    problem: str = Field(description="One-line statement of the problem")
    field: str = Field(description="The JSON field name the problem concerns")
    value: str = Field(description="The extracted value, or 'missing'")
    why: str = Field(description="One line on why this is a problem")


class InvoiceProblems(BaseModel):
    """
    The agent's full answer: every problem identified in one invoice.
    """

    problems: list[Problem] = Field(
        description="All identified problems. Empty when the invoice has none."
    )


class AgentState(MessagesState):
    """
    Graph state: conversation messages in, structured problems out.
    """

    problems: InvoiceProblems | None


llm = ChatOpenAI(
    model=os.environ["API_MODEL"],
    base_url=os.environ["API_URL"],
    api_key=SecretStr(os.environ["API_KEY"]),
)

# Structure the LLM's answer into the InvoiceProblems structure
structured_llm = llm.with_structured_output(InvoiceProblems)


def llm_call(state: AgentState):
    """Ask the LLM to identify the invoice's problems, structured."""

    messages = [SystemMessage(content=SYSTEM_PROMPT)] + state["messages"]
    response = structured_llm.invoke(messages)
    return {"problems": response}


# Build pipeline/workflow
agent_builder = StateGraph(AgentState)
agent_builder.add_node("llm_call", llm_call)
agent_builder.add_edge(START, "llm_call")
agent_builder.add_edge("llm_call", END)

# Compiling agent
agent = agent_builder.compile()


if __name__ == "__main__":
    data_dir = Path(__file__).resolve().parent.parent / "data" / "example_invoices"

    invoice = parse_invoice_xml(data_dir / "Faktura 209384.xml")
    invoice_json = json.dumps(invoice, indent=2, ensure_ascii=False)

    result = agent.invoke(
        AgentState(messages=[HumanMessage(content=invoice_json)], problems=None)
    )

    print(f"===== {invoice['source_file']} =====")
    problems = result["problems"]
    if len(problems.problems) == 0:
        print("No problems identified.")
    for problem in problems.problems:
        print(f"- {problem.problem}")
        print(f"    - Field: {problem.field}")
        print(f"    - Value: {problem.value}")
        print(f"    - Why:   {problem.why}")

    # print("\n===== Extracted data =====")
    print(invoice_json)

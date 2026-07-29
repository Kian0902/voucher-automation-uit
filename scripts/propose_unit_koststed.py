
import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path

from dotenv import load_dotenv
from pydantic import BaseModel, Field, SecretStr

from typing import Literal

from langchain_core.messages import SystemMessage
from langchain_core.messages import HumanMessage

from langchain_openai import ChatOpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import employees as emp
from stedkoder import STEDKODER
from anyascii import anyascii


load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")


DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "unit_koststed_proposals.json"

# ten units per call keeps the reply inside max_completion_tokens
BATCH_SIZE = 10


UNIT_MATCHING_PROMPT = """\
# Role
You match organisational unit names from UiT's employee pages against UiT's official
stedkode registry, so an employee can be booked to the right koststed.

# Task
You receive a list of unit names, each with the faculty or avdeling its page belongs to,
the campuses of its employees, and how many employees it has. For each one, give the
registry code that names the SAME unit, or null when the registry has no entry for it.

# Registry
These are the only codes that exist. Never return a code that is not in this list:

{registry}

# How to decide
- The same unit is often spelled differently ("Fiskerihøgskole" / "Fiskerihøyskole") or
  abbreviated ("HHT" for "Handelshøgskolen"). These are matches.
- A unit named for a campus must match the registry entry for that same campus, never a
  different one.
- Research groups, teams and study programmes usually have no code of their own. So do
  many local administrative units. Return null for them, that is a correct answer, not
  a failure. Their employees are booked to the parent unit instead.
- Never match on a shared word alone. "Seksjon for digital arbeidshverdag" and "Seksjon
  for renhold" share "Seksjon for" and are unrelated.
- The unit's faculty constrains the answer: a unit under one faculty cannot be a code
  belonging to another.

# Confidence
- high   : the names clearly denote the same unit
- medium : likely the same unit, but the wording leaves room for doubt
- low    : a guess worth a human's attention

Use low rather than null when you suspect a match but cannot support it, and null rather
than low when no registry entry plausibly denotes this unit.

# Rules
- Return one result per unit you were given, in the same order, with the unit name copied
  exactly as received
- Give the reason in one sentence: what made the names the same unit, or why no entry fits
- Do not invent codes or unit names
"""



class UnitMatch(BaseModel):
    """
    One unit name matched against the registry, or explicitly unmatched.
    """

    unit: str = Field(description="The unit name exactly as it was given to you")
    koststed: str | None = Field(description="The registry code, or null if none fits")
    confidence: Literal["high", "medium", "low"] = Field(description="How sure you are")
    reason: str = Field(description="One sentence on why")


class UnitMatches(BaseModel):
    """
    The model's answer for one batch of unit names.
    """
    thinking: str = Field(description="Your private step-by-step reasoning. Think here FIRST.")
    matches: list[UnitMatch] = Field(description="One result per unit given in order")


# local minmax-m3 model..
llm = ChatOpenAI(
    model=os.environ["API_MODEL"],
    base_url=os.environ["API_URL"],
    api_key=SecretStr(os.environ["API_KEY"]),
    temperature=0,
    timeout=360,
    max_completion_tokens=3000,
)

llm_matcher = llm.with_structured_output(UnitMatches, method="json_schema")





def _key(text: str | None):
    """
    Normalize a unit name for exact comparison
    Args:
        text (str | None): The unit name.

    Returns:
        str: Lowercased ASCII words separated by single spaces.
    """
    return " ".join(re.findall(r"\w+", anyascii(text or "").lower()))



def registry_listing():
    """
    Render the whole stedkode registry as lines for the prompt.

    Returns: str: One line per unit: code, type, campus and name.
    """
    lines = []

    for code in sorted(STEDKODER):
        info = STEDKODER[code]
        # data = code.append(sted_kode)
        campus = f" [{info['campus']}]" if "campus" in info else ""
        lines.append(f"{code:<10} {info['type']}{campus}: {info['name']}")

    return "\n".join(lines)


def unmatched_units():
    """
    Find the employee index units the registry does not name exactly.

    Returns:
        list[dict]: One entry per unit with the faculty its page belongs to and other related info.
    """

    registry_names = {_key(info["name"]) for info in STEDKODER.values()}

    # which faculty or avdeling each list page belongs to
    page_parent = {}

    for url, unit_name in emp.UNITS.items():
        for code, info in STEDKODER.items():

            if _key(info["name"]) == _key(unit_name):
                page_parent[url] = {"code": code, "name": info["name"]}
                break

    counts = Counter()
    campuses = {}
    parents = {}

    for employee in emp.EMPLOYEES:
        unit = employee["unit"]
        if unit is None or _key(unit) in registry_names:
            continue
        counts[unit] = counts[unit] + 1
        campuses.setdefault(unit, Counter())[employee["campus"]] += 1
        parents.setdefault(unit, page_parent.get(employee["source_url"]))

    units = []

    for unit, count in counts.most_common():
        parent = parents[unit]
        units.append({
            "unit": unit,
            "parent_code": parent["code"] if parent else None,
            "parent_name": parent["name"] if parent else None,
            "campuses": [c for c, _ in campuses[unit].most_common() if c is not None],
            "employees": count
        })

    return units


def propose_batch(units, registry_text):
    """
    Ask the model to match one batch of units against the registry.

    Args:
        units (list[dict]): Entries from unmatched_units().
        registry_text (str): The registry as rendered by registry_listing().

    Returns:
        list[dict]: One proposal per unit, each carrying the model's answer...
    """

    question = json.dumps(units, indent=2, ensure_ascii=False)
    messages = [
        SystemMessage(content=UNIT_MATCHING_PROMPT.format(registry=registry_text)),
        HumanMessage(content=question)
    ]
    response: UnitMatches = llm_matcher.invoke(messages)  # type: ignore

    # the model may name a code that does not exist so fix it by never passed on to the reviewer
    proposals = []
    for match in response.matches:
        code = match.koststed
        is_valid = code is None or code in STEDKODER
        proposals.append({
            "unit": match.unit,
            "koststed": code,
            "koststed_name": STEDKODER[code]["name"] if is_valid and code else None,
            "confidence": match.confidence,
            "reason": match.reason,
            "valid_code": is_valid,
        })

    return proposals



def propose_all(limit=None, batch_size=BATCH_SIZE):
    """
    Run the whole proposal pass, batch by batch.

    Args:
        limit (int | None): Only process this many units. None means all
        batch_size (int): Units per model call. Defaults to BATCH_SIZE

    Returns:
        list[dict]: Every proposal, in headcount order
    """

    units = unmatched_units()
    if limit is not None:
        units = units[:limit]

    registry_text = registry_listing()

    proposals = []
    for start in range(0, len(units), batch_size):
        batch = units[start:start + batch_size]
        batch_number = start // batch_size + 1
        total_batches = (len(units) + batch_size - 1) // batch_size

        print(f"batch {batch_number}/{total_batches}: {len(batch)} units", flush=True)
        batch_proposals = propose_batch(batch, registry_text)


    
        # keep the input context beside the answer so the review has both
        by_unit = {u["unit"]: u for u in batch}

        for proposal in batch_proposals:
            context = by_unit.get(proposal["unit"], {})

            proposal["employees"] = context.get("employees")
            proposal["campuses"] = context.get("campuses")
            proposal["parent_code"] = context.get("parent_code")
            proposal["parent_name"] = context.get("parent_name")

            flag = "" if proposal["valid_code"] else "  INVALID CODE"
            code_text = proposal["koststed"] or "none"

            print(f"  {code_text:<10} {proposal['confidence']:<6} "
                  f"{proposal['unit'][:45]:<45}{flag}", flush=True)

        proposals.extend(batch_proposals)

    return proposals





def main():


    parser = argparse.ArgumentParser(description="Propose a koststed for unmatched employee-index units.")

    parser.add_argument("--limit", type=int,
                        help="only process the N largest units (try a few first)")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE,
                        help=f"units per model call (default: {BATCH_SIZE})")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help=f"where to write the proposals (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()

    try:
        proposals = propose_all(limit=args.limit, batch_size=args.batch_size)
    except Exception as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    with open(args.output, "w", encoding="utf-8") as json_file:
        json.dump(proposals, json_file, indent=2, ensure_ascii=False)

    matched = [p for p in proposals if p["koststed"] is not None]
    invalid = [p for p in proposals if not p["valid_code"]]
    by_confidence = Counter(p["confidence"] for p in matched)

    print(f"\n{len(proposals)} units: {len(matched)} matched, "
          f"{len(proposals) - len(matched)} left to the parent unit")
    print(f"  confidence: {by_confidence['high']} high, "
          f"{by_confidence['medium']} medium, {by_confidence['low']} low")
    if len(invalid) > 0:
        print(f"  {len(invalid)} proposals name a code that is not in the registry "
              f"(marked valid_code false)")
    print(f"Wrote {args.output}")
    print("Review it before any of this reaches the resolver.")
    return 0


if __name__ == "__main__":
    # main()
    sys.exit(main())



"""
Probe the UiT ApiManager gateway with the calls the app makes, before the app gets a switch
for it. Kian runs this; it calls the gateway.

Usage:
    GATEWAY_KEY=sk-... python scripts/probe_gateway.py
    GATEWAY_KEY=sk-... GATEWAY_MODEL=gpt-5-mini python scripts/probe_gateway.py

Each check prints one line: OK or FAIL and what came back. The checks: the model list, a plain
Chat Completions call (the call LangChain makes), temperature on a reasoning model, the
reasoning effort "none", structured output with the finder's real schema and prompt on a
made-up row, the same through the Responses API, and the remaining kroner from the quota header.
Costs a few øre.
"""

import json
import os
import sys
import time
from pathlib import Path

from openai import OpenAI
from pydantic import SecretStr
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

# the gateway's OpenAI dialect, from its "Getting started" page
GATEWAY_URL = os.environ.get(
    "GATEWAY_URL", "https://apimanager-gjavbtbedsctbyg9.westeurope-01.azurewebsites.net/api/openai/v1"
)
GATEWAY_MODEL = os.environ.get("GATEWAY_MODEL", "gpt-6-luna")
GATEWAY_KEY = os.environ.get("GATEWAY_KEY", "")
QUOTA_HEADER = "x-quota-remaining-kr"
TIMEOUT_SECONDS = 120

# a row as the finder gets it, every value made up
MADE_UP_ROW = {
    "row_number": 1,
    "lev_nr": "123456",
    "leverandor": "Eksempel Leverandør AS",
    "fakturanr": "2026-1001",
    "deres_ref": "Ola Eksempel",
    "fakturadato": "01.11.2026",
    "ordrenr": "600000003",
    "organisasjonsnr": "999999999",
    "forfallsdato": "15.11.2026",
    "fakturabelop": "1 234,50",
    "status": "N",
    "kommentar": "",
}


def report(name: str, ok: bool, detail: str) -> None:
    """
    Print one check on one line.

    Args:
        name (str): What was checked.
        ok (bool): Whether it passed.
        detail (str): What came back, kept short.
    """

    verdict = "OK  "
    if not ok:
        verdict = "FAIL"
    print(f"{verdict} {name}: {detail}")


def error_text(error: Exception) -> str:
    """
    The first line of an error, with the HTTP status when there is one.

    Args:
        error (Exception): What the client raised.

    Returns:
        str: Short text for the report line.
    """

    status = getattr(error, "status_code", None)
    text = str(error).splitlines()[0][:160]
    if status is not None:
        return f"HTTP {status}: {text}"
    return text


def check_models(client: OpenAI) -> None:
    """Lists the gateway's models and says whether the chosen one is among them."""

    try:
        ids = []
        for model in client.models.list():
            ids.append(model.id)
    except Exception as error:
        report("model list", False, error_text(error))
        return
    report("model list", GATEWAY_MODEL in ids, f"{len(ids)} models, chosen one listed: {GATEWAY_MODEL in ids}")


def check_chat(client: OpenAI, name: str, **extra) -> None:
    """
    One plain Chat Completions call with extra parameters, and the quota header.

    Args:
        client (OpenAI): The client.
        name (str): The report line's name.
        **extra: Parameters added to the call, e.g. temperature=0.1.
    """

    try:
        raw = client.chat.completions.with_raw_response.create(
            model=GATEWAY_MODEL,
            messages=[{"role": "user", "content": "Svar med ett ord: hva er hovedstaden i Norge?"}],
            max_completion_tokens=200,
            **extra,
        )
    except Exception as error:
        report(name, False, error_text(error))
        return
    completion = raw.parse()
    answer = completion.choices[0].message.content
    remaining = raw.headers.get(QUOTA_HEADER, "header missing")
    report(name, True, f"answer {answer!r}, usage {completion.usage.total_tokens} tokens, {QUOTA_HEADER}={remaining}")


def check_structured(use_responses_api: bool) -> None:
    """
    The finder's real schema and prompt on the made-up row, through LangChain as the app does.

    Args:
        use_responses_api (bool): False for Chat Completions (what the app uses), True for the
                                  gateway's documented Responses API.
    """

    # the agent builds its own client at import from .env.local; placeholders keep that quiet
    for key in ["API_URL", "API_MODEL", "API_KEY"]:
        os.environ.setdefault(key, "unused")
    from list_agent import RowValues, SYSTEM_PROMPT_FIND

    name = "structured output, Chat Completions"
    if use_responses_api:
        name = "structured output, Responses API"
    llm = ChatOpenAI(
        model=GATEWAY_MODEL,
        base_url=GATEWAY_URL,
        api_key=SecretStr(GATEWAY_KEY),
        timeout=TIMEOUT_SECONDS,
        max_completion_tokens=7000,
        reasoning_effort="none",
        use_responses_api=use_responses_api,
    )
    finder = llm.with_structured_output(RowValues, include_raw=True)
    messages = [
        SystemMessage(content=SYSTEM_PROMPT_FIND),
        HumanMessage(content=json.dumps(MADE_UP_ROW, indent=2, ensure_ascii=False)),
    ]
    started = time.monotonic()
    try:
        result = finder.invoke(messages)
    except Exception as error:
        report(name, False, error_text(error))
        return
    seconds = time.monotonic() - started
    parsed = result["parsed"]
    if parsed is None:
        report(name, False, f"answer did not fit the schema: {str(result['parsing_error'])[:160]}")
        return
    usage = getattr(result["raw"], "usage_metadata", None) or {}
    values = []
    for item in parsed.lookup_values:
        values.append(item.model_dump())
    report(name, True, f"{seconds:.1f} s, usage {usage.get('input_tokens')}+{usage.get('output_tokens')} tokens, "
                       f"thinking {len(parsed.thinking)} chars, values {values}")


if __name__ == "__main__":
    if GATEWAY_KEY == "":
        print("set GATEWAY_KEY to a key from the gateway's API Keys page")
        sys.exit(1)
    print(f"gateway {GATEWAY_URL}\nmodel   {GATEWAY_MODEL}\n")

    client = OpenAI(base_url=GATEWAY_URL, api_key=GATEWAY_KEY, timeout=TIMEOUT_SECONDS)
    check_models(client)
    check_chat(client, "plain Chat Completions")
    # a reasoning model is expected to refuse temperature; the app sends 0.1 to maggie today
    check_chat(client, "temperature=0.1 (FAIL expected on a reasoning model)", temperature=0.1)
    check_chat(client, "reasoning_effort=none", reasoning_effort="none")
    check_chat(client, "reasoning_effort=low", reasoning_effort="low")
    check_structured(use_responses_api=False)
    check_structured(use_responses_api=True)

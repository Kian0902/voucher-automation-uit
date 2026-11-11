"""
The chat model client both agents use, built for the model server the settings point at.

Two servers are known. "maggie" is the UiT Machine Learning group's server, which serves
DeepSeek and takes the sampling parameters measured there. "gateway" is UiT's ApiManager in
front of OpenAI's EU-hosted models; its reasoning models refuse temperature and take a reasoning
effort instead (probed 2026-11-11 with gpt-6-luna: effort "none" keeps the reasoning in the
schema's thinking field, as on maggie). The environment holds MODEL_SERVER and both servers'
address, model and key; the app's settings pages write them.
"""

import os

from pydantic import SecretStr
from langchain_openai import ChatOpenAI


MODEL_SERVERS = {
    "maggie": "maggie (UiT ML-gruppen)",
    "gateway": "GPT-6 Luna (UiT ApiManager)",
}
DEFAULT_MODEL_SERVER = "maggie"
# the environment variables that hold each server's address, model and key
SERVER_KEYS = {
    "maggie": ["API_URL", "API_MODEL", "API_KEY"],
    "gateway": ["GATEWAY_API_URL", "GATEWAY_API_MODEL", "GATEWAY_API_KEY"],
}
TIMEOUT_SECONDS = 120
MAX_COMPLETION_TOKENS = 7000


def current_model_server() -> str:
    """
    The chosen server's name.

    Returns:
        str: MODEL_SERVER from the environment, "maggie" when it is unset or unknown.
    """

    name = os.environ.get("MODEL_SERVER", "")
    if name not in MODEL_SERVERS:
        return DEFAULT_MODEL_SERVER
    return name


def build_chat_model() -> ChatOpenAI:
    """
    The client for the chosen server, from its three environment variables.

    Returns:
        ChatOpenAI: Bounded timeout and output, two retries; temperature 0.1 on maggie,
                    reasoning effort "none" and no temperature on the gateway.

    Example:
        os.environ["MODEL_SERVER"] = "gateway"; build_chat_model().reasoning_effort == "none"
    """

    server = current_model_server()
    url_key, model_key, key_key = SERVER_KEYS[server]
    if server == "gateway":
        return ChatOpenAI(
            model=os.environ[model_key],
            base_url=os.environ[url_key],
            api_key=SecretStr(os.environ[key_key]),
            reasoning_effort="none",  # the gateway's reasoning models refuse temperature (probed)
            timeout=TIMEOUT_SECONDS,
            max_completion_tokens=MAX_COMPLETION_TOKENS,
            max_retries=2,
        )
    return ChatOpenAI(
        model=os.environ[model_key],
        base_url=os.environ[url_key],
        api_key=SecretStr(os.environ[key_key]),
        temperature=0.1,  # 0.0 spirals, and frequency_penalty corrupted verbatim values (measured): do not reintroduce either
        timeout=TIMEOUT_SECONDS,
        max_completion_tokens=MAX_COMPLETION_TOKENS,
        max_retries=2,
    )


if __name__ == "__main__":
    # no server is called: the clients are only built
    for key in ["API_URL", "API_MODEL", "API_KEY", "GATEWAY_API_URL", "GATEWAY_API_MODEL", "GATEWAY_API_KEY"]:
        os.environ[key] = "x"
    os.environ["API_URL"] = "http://maggie.test/v1"
    os.environ["GATEWAY_API_URL"] = "http://gateway.test/v1"

    os.environ["MODEL_SERVER"] = "nonsense"
    assert current_model_server() == "maggie"
    maggie = build_chat_model()
    assert maggie.temperature == 0.1 and maggie.reasoning_effort is None
    assert maggie.openai_api_base == "http://maggie.test/v1", maggie.openai_api_base

    os.environ["MODEL_SERVER"] = "gateway"
    gateway = build_chat_model()
    assert gateway.temperature is None and gateway.reasoning_effort == "none", (gateway.temperature, gateway.reasoning_effort)
    assert gateway.openai_api_base == "http://gateway.test/v1"
    print("model_client self-check passed")

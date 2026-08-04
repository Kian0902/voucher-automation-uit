


import os
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")





API_BASE_URL = os.environ.get("LOOKUP_API_URL", "http://127.0.0.1:8000")
TIMEOUT_SECONDS = 10

def check_api_up() -> dict:
    """
    Checks that the lookup API is running and can see the database.

    Returns: dict: The /health response with the table counts.
    Raises: RuntimeError: When the API does not answer, with the command to start it.
    """

    try:
        response = httpx.get(f"{API_BASE_URL}/health", timeout=TIMEOUT_SECONDS)
    except httpx.ConnectError as error:
        raise RuntimeError(
            f"lookup API not reachable on {API_BASE_URL}. Start it with: python api/app.py"
        ) from error

    return response.json()


def lookup_io(io: str) -> dict:
    """
    Looks up one IO (innkjøpsordrenummer) in report B through the API.

    Args:  io (str): The IO from the invoice, normally 9 digits starting with 6.
    Returns:
        dict: The API response. status "found" carries the koststed. "not_found" means
        the IO is unknown to Unit4.
    """

    encoded_io = quote(io, safe="")
    response = httpx.get(f"{API_BASE_URL}/lookup/io/{encoded_io}", timeout=TIMEOUT_SECONDS)
    return response.json()


def lookup_person(name: str) -> dict:
    """
    Looks up a person name in report A through the API.

    Args:  name (str): The person name as written on the invoice.
    Returns:
        dict: The API response. match_type "exact" or "partial".
    """

    response = httpx.get(f"{API_BASE_URL}/lookup/person", params={"name": name}, timeout=TIMEOUT_SECONDS)
    return response.json()



def lookup_koststed(code: str) -> dict:
    """
    Looks up one koststed in report C through the API.

    Args:  code (str): The koststed from the invoice, normally 8 digits.
    Returns:
        dict: The API response.
    """

    encoded_code = quote(code, safe="")
    response = httpx.get(f"{API_BASE_URL}/lookup/koststed/{encoded_code}", timeout=TIMEOUT_SECONDS)
    return response.json()



# registry for the agents tool dispatch
TOOLS = {
    "lookup_io": lookup_io,
    "lookup_person": lookup_person,
    "lookup_koststed": lookup_koststed,
}



if __name__ == "__main__":

    health = check_api_up()
    assert health["status"] == "ok"

    result_koststed = lookup_koststed("123")
    assert result_koststed["status"] == "invalid_format"

    result_person = lookup_person("...")
    assert result_person["match_type"] == "none"

    print("lookup_tools test passed")

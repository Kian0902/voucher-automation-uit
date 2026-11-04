"""
The lookups the agents may call, and the registry the resolver's tool dispatch reads.

The functions are thin wrappers around src/lookup_data.py, which holds the three reports in
memory. They keep the names and result dicts of the old HTTP tools, so the prompts and the
execute_tool nodes did not change when the database went away.
"""

import lookup_data


def check_data_loaded() -> dict:
    """
    Loads the three reports if they are not loaded yet and reports their row counts.

    Called once at the start of a run, where the old health check of the lookup API was.

    Returns:
        dict: status "ok" and the row counts per table.

    Raises:
        FileNotFoundError: When a report CSV is missing from the data folder.
        ValueError: When a report has another column layout than expected.
    """

    return lookup_data.health()


def lookup_io(io: str) -> dict:
    """
    Looks up one IO (innkjøpsordrenummer) in report B.

    Args:
        io (str): The IO from the invoice or the list, normally 9 digits starting with 6.

    Returns:
        dict: status "found" carries the koststed. "not_found" means the IO is unknown to Unit4.
    """

    return lookup_data.lookup_io(io)


def lookup_person(name: str) -> dict:
    """
    Looks up a person name in report A.

    Args:
        name (str): The person name as written on the invoice or the list.

    Returns:
        dict: match_type "exact", "partial", "partial_relaxed", "too_many" or "none".
    """

    return lookup_data.lookup_person(name)


def lookup_koststed(code: str) -> dict:
    """
    Looks up one koststed in report C, the catalog.

    Args:
        code (str): The koststed from the invoice or the list, normally 8 digits.

    Returns:
        dict: status "found", "not_in_catalog" or "invalid_format".
    """

    return lookup_data.lookup_koststed(code)


# registry for the agents' tool dispatch
TOOLS = {
    "lookup_io": lookup_io,
    "lookup_person": lookup_person,
    "lookup_koststed": lookup_koststed,
}


if __name__ == "__main__":

    health = check_data_loaded()
    assert health["status"] == "ok"
    assert health["table_counts"]["persons"] > 0

    result_koststed = lookup_koststed("123")
    assert result_koststed["status"] == "invalid_format"

    result_person = lookup_person("...")
    assert result_person["match_type"] == "none"

    assert set(TOOLS) == {"lookup_io", "lookup_person", "lookup_koststed"}

    print("lookup_tools test passed")

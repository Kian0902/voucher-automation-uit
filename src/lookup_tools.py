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
    Looks up a name among the persons of report A and the koststed names of report C.

    Args:
        name (str): The name as written on the invoice or the list.

    Returns:
        dict: match_type "exact", "partial", "partial_relaxed", "too_many" or "none".
    """

    return lookup_data.lookup_person(name)


# longer texts are cut in the hit, the agent reads the whole invoice anyway
TEXT_LIMIT = 300


def names_in_invoice(invoice: dict) -> list:
    """
    Finds report A names in every text of an extracted invoice, for the agent to judge.

    Walks the whole dict, so a name is found wherever it stands: a note, a line's item name, a
    contact field. Each hit says where it stood and quotes the text, since only the context tells
    whether the person ordered, travelled or is a contact at the supplier. The koststed is left
    out on purpose: the agent looks the name up, so every suggested value still comes from a tool
    result. The file name is skipped, it is not invoice content.

    Args:
        invoice (dict): The dict from xml_processor.parse_invoice_xml().

    Returns:
        list[dict]: One dict per name and place, with name, found_in and text.

    Example:
        A line whose item name holds a known person gives
        {"name": "<name as in report A>", "found_in": "lines[0].item_name", "text": "<the item name>"}
    """

    hits = []
    for key, value in invoice.items():
        if key == "source_file":
            continue
        _scan_value(value, key, hits)
    return hits


def _scan_value(value, path: str, hits: list) -> None:
    """
    Walk one value of the invoice dict and append a hit for every known name in its texts.

    Args:
        value: A dict, a list, a string or any scalar from the invoice dict.
        path (str): Where the value stands, in JSON notation such as "lines[0].item_name".
        hits (list): The list being filled.
    """

    if isinstance(value, dict):
        for key, child in value.items():
            _scan_value(child, f"{path}.{key}", hits)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _scan_value(child, f"{path}[{index}]", hits)
    elif isinstance(value, str):
        for person in lookup_data.find_names_in_text(value):
            hits.append({"name": person["name"], "found_in": path, "text": value[:TEXT_LIMIT]})


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

"""
The predefined structure for one screenshot of the ERP's list of blocked invoices.
"""

import csv
import io
import json


# The eleven named columns, columns 4 to 14 on screen, in the ERP's order. The key is the
# header transliterated to ASCII; the header text is kept for matching an OCR'd header row.
COLUMNS = [
    ("lev_nr", "Lev.nr"),
    ("leverandor", "Leverandør"),
    ("fakturanr", "Fakturanr"),
    ("deres_ref", "Deres ref."),
    ("fakturadato", "Fakturadato"),
    ("ordrenr", "Ordrenr"),
    ("organisasjonsnr", "Organisasjonsnr"),
    ("forfallsdato", "Forfallsdato"),
    ("fakturabelop", "Fakturabeløp (inkl. mva.)"),
    ("status", "Status"),
    ("kommentar", "Kommentar"),
]

COLUMN_KEYS = []
for key, header in COLUMNS:
    COLUMN_KEYS.append(key)

# Row colours in the ERP: yellow rows hold most required information, red rows miss key information.
CATEGORIES = ("yellow", "red")

# What the stage 2 agent adds to a row: the koststeder a lookup returned, a label for the caseworker
# and the agent's explanation. The labels: one koststed directly supported by a lookup, one koststed
# to confirm, more than one found, none found.
RESULT_LABELS = ("løst", "må bekreftes", "flere funnet", "uløst")

# The columns of the overview sheet: the ERP list as on screen, then what the agent found,
# its reasoning and what the row cost.
CSV_KEYS = ["row_number"] + COLUMN_KEYS + ["funnet_koststed", "begrunnelse", "tid_brukt", "tokens_brukt"]


def new_row(row_number: int) -> dict:
    """
    Build an empty row with its row number set, every column None and every confidence None.

    Args:
        row_number (int): The number shown in the first column on screen.

    Returns:
        dict: {"row_number": row_number, "lev_nr": None, ..., "kommentar": None,
              "confidence": {"lev_nr": None, ..., "kommentar": None}}.

    Example:
        new_row(7)["row_number"] == 7
    """
    row = {"row_number": row_number}
    for key in COLUMN_KEYS:
        row[key] = None
    confidence = {}
    for key in COLUMN_KEYS:
        confidence[key] = None
    row["confidence"] = confidence
    return row


def new_list(source_file: str, category: str | None) -> dict:
    """
    Build the structure for one screenshot, with no rows yet.

    Args:
        source_file (str): File name of the screenshot.
        category (str | None): "yellow", "red", or None when not known.
        
    Returns:
        dict: {"source_file": source_file, "category": category, "rows": []}.

    Raises:
        ValueError: If category is neither a known category nor None.
    """
    if category is not None and category not in CATEGORIES:
        raise ValueError(f"unknown category {category!r}, expected one of {CATEGORIES} or None")
    erp_list = {
        "source_file": source_file,
        "category": category,
        "rows": [],
    }
    return erp_list


def new_resolved_row(row: dict) -> dict:
    """
    Copy an extracted row and add the fields the agent fills.

    Args:
        row (dict): A row from new_row(), filled by the parser.

    Returns:
        dict: The same keys and values, then "funnet_koststed": [], "resultat": None,
              "begrunnelse": None, "tid_brukt": None, "tokens_brukt": None. The confidence
              dict is copied, not shared.

    Example:
        new_resolved_row(new_row(7))["funnet_koststed"] == []
    """
    resolved_row = {}
    for key, value in row.items():
        if key == "confidence":
            resolved_row[key] = dict(value)
        else:
            resolved_row[key] = value
    resolved_row["funnet_koststed"] = []
    resolved_row["resultat"] = None
    resolved_row["begrunnelse"] = None
    resolved_row["tid_brukt"] = None
    resolved_row["tokens_brukt"] = None
    return resolved_row


def to_json(erp_list: dict) -> str:
    """
    Serialize one screenshot's structure as readable JSON, Norwegian letters kept.

    Args:
        erp_list (dict): The dict from new_list(), with its rows filled in.

    Returns:
        str: Indented JSON text.
    """
    json_text = json.dumps(erp_list, indent=2, ensure_ascii=False)
    return json_text


def to_csv(resolved_list: dict) -> str:
    """
    Serialize one screenshot's resolved rows as a CSV sheet for a spreadsheet program.

    Args:
        resolved_list (dict): The structure from list_agent.resolve_list().

    Returns:
        str: A header row and one line per row, the CSV_KEYS columns, comma separated.
             The koststeder are joined with ", "; the begrunnelse keeps its line breaks
             inside the cell; None becomes an empty cell.
    """
    buffer = io.StringIO()
    # a value holding a comma, such as an amount, is quoted by the writer
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(CSV_KEYS)
    for row in resolved_list["rows"]:
        values = []
        for key in CSV_KEYS:
            value = row[key]
            if key == "funnet_koststed":
                value = ", ".join(value)
            if value is None:
                value = ""
            values.append(value)
        writer.writerow(values)
    csv_text = buffer.getvalue()
    return csv_text


if __name__ == "__main__":
    assert len(COLUMNS) == 11, "the ERP list has eleven named columns"
    assert len(set(COLUMN_KEYS)) == 11, "column keys must be unique"
    for key in COLUMN_KEYS:
        assert key.isascii(), f"key {key!r} must be ASCII"

    row = new_row(1)
    assert list(row.keys()) == ["row_number"] + COLUMN_KEYS + ["confidence"]
    assert row["row_number"] == 1
    for key in COLUMN_KEYS:
        assert row[key] is None
        assert row["confidence"][key] is None

    resolved_row = new_resolved_row(row)
    assert list(resolved_row.keys()) == list(row.keys()) + ["funnet_koststed", "resultat", "begrunnelse", "tid_brukt", "tokens_brukt"]
    assert resolved_row["confidence"] is not row["confidence"], "the confidence dict must be copied"
    assert resolved_row["funnet_koststed"] == [] and resolved_row["resultat"] is None
    assert len(RESULT_LABELS) == 4

    erp_list = new_list("Skjermbilde YYYY-MM-DD HHMMSS.png", "yellow")
    erp_list["rows"].append(row)
    round_trip = json.loads(to_json(erp_list))
    assert round_trip == erp_list, "JSON must round-trip unchanged"

    resolved_row["funnet_koststed"] = ["10000000", "20000000"]
    resolved_row["begrunnelse"] = "line one\nline two"
    resolved_row["tid_brukt"] = 45
    resolved_row["tokens_brukt"] = 9226
    resolved_row["fakturabelop"] = "1 234,50"
    resolved_list = {"source_file": erp_list["source_file"], "category": "yellow", "rows": [resolved_row]}
    csv_rows = list(csv.reader(io.StringIO(to_csv(resolved_list))))
    assert csv_rows[0] == CSV_KEYS, "the header row must be the CSV_KEYS"
    assert len(csv_rows) == 2, "one header row and one data row"
    assert csv_rows[1][CSV_KEYS.index("row_number")] == "1"
    assert csv_rows[1][CSV_KEYS.index("lev_nr")] == "", "None must become an empty cell"
    assert csv_rows[1][CSV_KEYS.index("fakturabelop")] == "1 234,50", "a comma inside a value must survive"
    assert csv_rows[1][CSV_KEYS.index("funnet_koststed")] == "10000000, 20000000"
    assert csv_rows[1][CSV_KEYS.index("begrunnelse")] == "line one\nline two", "line breaks survive inside the cell"
    assert csv_rows[1][CSV_KEYS.index("tid_brukt")] == "45"
    assert csv_rows[1][CSV_KEYS.index("tokens_brukt")] == "9226"

    try:
        new_list("x.png", "green")
        raise AssertionError("an unknown category must be rejected")
    except ValueError:
        pass

    print(to_json(erp_list))
    print("erp_list self-check OK")

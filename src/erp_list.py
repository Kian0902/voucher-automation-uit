"""
The predefined structure for one screenshot of the ERP's list of blocked invoices.
"""

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

    erp_list = new_list("Skjermbilde YYYY-MM-DD HHMMSS.png", "yellow")
    erp_list["rows"].append(row)
    round_trip = json.loads(to_json(erp_list))
    assert round_trip == erp_list, "JSON must round-trip unchanged"

    try:
        new_list("x.png", "green")
        raise AssertionError("an unknown category must be rejected")
    except ValueError:
        pass

    print(to_json(erp_list))
    print("erp_list self-check OK")

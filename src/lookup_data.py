"""
The three report CSVs loaded into memory once, and the five lookups built on them.

Replaces the PostgreSQL database and the lookup API. The result dicts are the ones the API
returned, key for key, so the prompts, the agents and the UI see no difference. The files are
read on the first lookup or on an explicit load_tables() call at app start, and not again until
the process restarts.
"""

import csv
import re
import time
from pathlib import Path

from normalize import normalize_name


REPORTS_DIR = Path(__file__).resolve().parent.parent / "data" / "koststed_tables"
REPORT_A = REPORTS_DIR / "Ansattreferanse_med_koststed.csv"
REPORT_B = REPORTS_DIR / "Innkjøpsordrenummer_med_koststed.csv"
REPORT_C = REPORTS_DIR / "Koststed_med_beskrivelse.csv"

# The column layout is assumed by the loaders, so a new export is checked against it first.
HEADER_A = ["Navn", "Koststedsnr.", "Koststedsnavn"]
HEADER_B = ["Innkjøpsordrenr (IO hode)", "Koststedsnr. (IO hode)", "Koststedsnavn (IO hode)"]
HEADER_C = ["Koststedsnr.", "Koststedsnavn"]

# More candidates than this is not a suggestion, the caseworker gets "too_many" instead.
MAX_CANDIDATES = 10

IO_PATTERN = re.compile(r"6\d{8}")
KOSTSTED_PATTERN = re.compile(r"\d{8}")


# TABLES

class LookupTables:
    """
    The loaded reports and the indexes the lookups read.

    persons holds one dict per usable row of report A in file order, with the response fields
    name, koststed and koststed_name plus the normalized key and the set of its words.
    persons_by_key and persons_by_word map to positions in that list, so every search returns
    persons in file order, as the database did.
    """

    def __init__(self):
        self.persons = []
        self.persons_by_key = {}
        self.persons_by_word = {}
        self.ios = {}
        self.ios_by_koststed = {}
        self.koststeder = {}
        self.skipped_names = 0
        self.unknown_ios = 0
        self.skipped_koststeder = []


_TABLES = None


# LOADING

def read_rows(csv_path: Path, expected_header: list) -> list:
    """
    Reads one report CSV and checks its header against the expected column layout.

    Args:
        csv_path (Path): Path to the report CSV.
        expected_header (list[str]): The column names the loader assumes.

    Returns:
        list[list[str]]: The data rows, without the header.

    Raises:
        ValueError: When the header differs or the file has no data rows.
    """

    with open(csv_path, encoding="utf-8-sig", newline="") as csv_file:
        first_line = csv_file.readline()
        csv_file.seek(0)
        # Excel on a Norwegian PC writes "CSV UTF-8" with semicolons; the header line tells which
        delimiter = ","
        if first_line.count(";") > first_line.count(","):
            delimiter = ";"
        reader = csv.reader(csv_file, delimiter=delimiter)
        all_rows = list(reader)

    if len(all_rows) == 0:
        raise ValueError(f"{csv_path.name} is empty")

    # Excel also writes the empty columns to the right of the data, as trailing separators on
    # every line; those blank cells are not columns
    for row in all_rows:
        while len(row) > 0 and row[-1].strip() == "":
            row.pop()

    header = []
    for column_name in all_rows[0]:
        header.append(column_name.strip())
    # a header pasted as one comma-joined cell above semicolon rows (seen in an export of
    # 2026-11-11): split it on the other separator
    if len(header) == 1 and len(expected_header) > 1:
        other_delimiter = ","
        if delimiter == ",":
            other_delimiter = ";"
        header = []
        for column_name in all_rows[0][0].split(other_delimiter):
            header.append(column_name.strip())
    if header != expected_header:
        raise ValueError(f"{csv_path.name} has the columns {header}, expected {expected_header}")

    data_rows = all_rows[1:]
    if len(data_rows) == 0:
        raise ValueError(f"{csv_path.name} has a header but no rows")

    return data_rows


def _cell(row: list, index: int) -> str | None:
    """
    Gets one cell value from a row, stripped.

    Args:
        row (list[str]): A data row from read_rows().
        index (int): Column index.

    Returns:
        str | None: The value, or None when the cell is blank.
    """

    # a row ends early when its last columns were blank, since read_rows drops trailing blanks
    if index >= len(row):
        return None
    raw_value = row[index]
    stripped_value = raw_value.strip()
    if stripped_value == "":
        return None
    return stripped_value


def _is_koststed(value: str) -> bool:
    """
    Tells whether a value has the koststed format, exactly 8 digits.

    Args:
        value (str): The value to test.

    Returns:
        bool: True when the value is 8 digits.
    """

    return KOSTSTED_PATTERN.fullmatch(value) is not None


def load_persons(data_rows: list, tables: LookupTables) -> None:
    """
    Fills the persons list and its two indexes from report A.

    Rows without a usable name, empty or nothing left after normalizing, can never be looked up,
    so they are skipped and counted.

    Args:
        data_rows (list[list[str]]): Data rows of report A.
        tables (LookupTables): The tables being built.
    """

    for row in data_rows:
        original_name = _cell(row, 0)
        koststed = _cell(row, 1)
        koststed_name = _cell(row, 2)

        if original_name is None:
            tables.skipped_names = tables.skipped_names + 1
            continue

        key = normalize_name(original_name)
        if key == "":
            tables.skipped_names = tables.skipped_names + 1
            continue

        words = set(key.split(" "))
        position = len(tables.persons)
        tables.persons.append({
            "name": original_name,
            "koststed": koststed,
            "koststed_name": koststed_name,
            "key": key,
            "words": words,
        })

        if key not in tables.persons_by_key:
            tables.persons_by_key[key] = []
        tables.persons_by_key[key].append(position)

        for word in words:
            if word not in tables.persons_by_word:
                tables.persons_by_word[word] = set()
            tables.persons_by_word[word].add(position)


def load_ios(data_rows: list, tables: LookupTables) -> None:
    """
    Fills the ios dict and the per-koststed IO lists from report B.

    Rows where the koststed is not 8 digits (the "Ukjent" ones) keep their IO but get koststed
    None and the koststed_unknown flag instead. An IO that occurs twice keeps its first row.

    Args:
        data_rows (list[list[str]]): Data rows of report B.
        tables (LookupTables): The tables being built.
    """

    for row in data_rows:
        io = _cell(row, 0)
        koststed = _cell(row, 1)
        koststed_name = _cell(row, 2)

        if io is None:
            continue

        koststed_is_unknown = koststed is None or not _is_koststed(koststed)
        if koststed_is_unknown:
            tables.unknown_ios = tables.unknown_ios + 1
            koststed = None
            koststed_name = None

        if io in tables.ios:
            continue

        tables.ios[io] = {
            "koststed": koststed,
            "koststed_name": koststed_name,
            "koststed_unknown": koststed_is_unknown,
        }

        if koststed is not None:
            if koststed not in tables.ios_by_koststed:
                tables.ios_by_koststed[koststed] = []
            tables.ios_by_koststed[koststed].append(io)

    # the API listed the IOs of a koststed in ascending order
    for koststed in tables.ios_by_koststed:
        tables.ios_by_koststed[koststed].sort()


def load_koststeder(data_rows: list, tables: LookupTables) -> None:
    """
    Fills the koststed catalog from report C.

    Rows where the koststed is not 8 digits (the "Ukjent" row) are not real koststeder, so they
    are skipped and remembered.

    Args:
        data_rows (list[list[str]]): Data rows of report C.
        tables (LookupTables): The tables being built.
    """

    for row in data_rows:
        koststed = _cell(row, 0)
        description = _cell(row, 1)

        if koststed is None or not _is_koststed(koststed):
            tables.skipped_koststeder.append(koststed)
            continue

        tables.koststeder[koststed] = description


def load_tables(reports_dir: Path = REPORTS_DIR) -> LookupTables:
    """
    Reads the three reports from a folder and makes them the tables every lookup uses.

    Args:
        reports_dir (Path): The folder holding the three report CSVs.

    Returns:
        LookupTables: The loaded tables, also kept in the module for the lookups.

    Raises:
        FileNotFoundError: When a report file is missing.
        ValueError: When a header differs from the expected layout.
    """

    global _TABLES

    tables = LookupTables()
    rows_a = read_rows(reports_dir / REPORT_A.name, HEADER_A)
    rows_b = read_rows(reports_dir / REPORT_B.name, HEADER_B)
    rows_c = read_rows(reports_dir / REPORT_C.name, HEADER_C)
    load_persons(rows_a, tables)
    load_ios(rows_b, tables)
    load_koststeder(rows_c, tables)

    _TABLES = tables
    return tables


def get_tables() -> LookupTables:
    """
    Returns the loaded tables, loading them from the default folder on the first call.

    Returns:
        LookupTables: The tables every lookup reads.
    """

    if _TABLES is None:
        load_tables()
    return _TABLES


# LOOKUPS

def _person_dicts(tables: LookupTables, positions: list) -> list:
    """
    Turns person positions into response dicts.

    Args:
        tables (LookupTables): The loaded tables.
        positions (list[int]): Positions in tables.persons.

    Returns:
        list[dict]: One dict per person with name, koststed and koststed_name.
    """

    persons = []
    for position in positions:
        person = tables.persons[position]
        persons.append({
            "name": person["name"],
            "koststed": person["koststed"],
            "koststed_name": person["koststed_name"],
        })
    return persons


def _persons_with_all_words(tables: LookupTables, words: list) -> list:
    """
    Finds every person whose name contains all the given words, in file order.

    Args:
        tables (LookupTables): The loaded tables.
        words (list[str]): Normalized query words; duplicates do not matter.

    Returns:
        list[int]: Positions in tables.persons, ascending.
    """

    matching_positions = None
    for word in words:
        positions_with_word = tables.persons_by_word.get(word)
        if positions_with_word is None:
            return []
        if matching_positions is None:
            matching_positions = set(positions_with_word)
        else:
            matching_positions = matching_positions & positions_with_word

    if matching_positions is None:
        return []

    ordered_positions = sorted(matching_positions)
    return ordered_positions


def health() -> dict:
    """
    Reports that the tables are loaded and how many rows each holds.

    Returns:
        dict: status "ok" and the row counts per table.
    """

    tables = get_tables()
    counts = {
        "persons": len(tables.persons),
        "ios": len(tables.ios),
        "koststeder": len(tables.koststeder),
    }
    return {"status": "ok", "table_counts": counts}


def lookup_io(io: str) -> dict:
    """
    Looks up one IO (innkjøpsordrenummer) in report B.

    Args:
        io (str): The IO to look up, normally 9 digits starting with 6.

    Returns:
        dict: status is "found", "found_unknown_koststed" or "not_found".
    """

    tables = get_tables()
    stripped_io = io.strip()
    format_valid = IO_PATTERN.fullmatch(stripped_io) is not None

    entry = tables.ios.get(stripped_io)
    if entry is None:
        message_note = "IO is not in report B, so it is unknown to Unit4"
        return {"query": stripped_io, "format_valid": format_valid, "status": "not_found", "note": message_note}

    if entry["koststed_unknown"]:
        message_note = "the IO exists but report B lists its koststed as unknown"
        return {"query": stripped_io, "format_valid": format_valid, "status": "found_unknown_koststed", "note": message_note}

    return {"query": stripped_io, "format_valid": format_valid, "status": "found",
        "koststed": entry["koststed"], "koststed_name": entry["koststed_name"]}


def _relaxed_person_search(tables: LookupTables, name: str, key: str, tokens: list) -> dict:
    """
    Third search method where one query word may be wrong or extra.

    Retries the word search once per query word with that word left out. Needs at least three
    query words. A single hit can serve as the resolution; several hits are candidates for the
    caseworker.

    Args:
        tables (LookupTables): The loaded tables.
        name (str): The original query name.
        key (str): The normalized key of the query.
        tokens (list[str]): The words of the normalized key.

    Returns:
        dict: match_type is "partial_relaxed", "too_many" or "none".
    """

    if len(tokens) < 3:
        return {"query": name, "normalized_key": key, "match_type": "none", "persons": []}

    relaxed_positions = []
    seen_persons = []
    for drop_index in range(len(tokens)):
        reduced_tokens = tokens[:drop_index] + tokens[drop_index + 1:]
        subset_positions = _persons_with_all_words(tables, reduced_tokens)

        # collect every hit once, even when several reduced queries find it
        for position in subset_positions:
            person = tables.persons[position]
            person_id = f"{person['name']}|{person['koststed']}"
            if person_id not in seen_persons:
                seen_persons.append(person_id)
                relaxed_positions.append(position)

    if len(relaxed_positions) == 0:
        return {"query": name, "normalized_key": key, "match_type": "none", "persons": []}

    if len(relaxed_positions) > MAX_CANDIDATES:
        return {"query": name, "normalized_key": key, "match_type": "too_many", "persons": [],
            "count": len(relaxed_positions),
            "note": f"more than {MAX_CANDIDATES} names match all but one query word"}

    return {"query": name, "normalized_key": key, "match_type": "partial_relaxed",
        "persons": _person_dicts(tables, relaxed_positions),
        "note": "these names match all but one query word"}


def lookup_person(name: str) -> dict:
    """
    Looks up a person name in report A with three search methods.

    Exact: the whole normalized name matches. Partial: every query word appears in the name, in
    any order, two words at least. Relaxed: all but one query word appear, three words at least.

    Args:
        name (str): The person name as written on the invoice or the list.

    Returns:
        dict: match_type is "exact", "partial", "partial_relaxed", "none" or "too_many".
    """

    tables = get_tables()
    key = normalize_name(name)

    if key == "":
        message_note = "nothing left of the name after normalization"
        return {"query": name, "normalized_key": key, "match_type": "none", "persons": [], "note": message_note}

    exact_positions = tables.persons_by_key.get(key, [])
    if len(exact_positions) > 0:
        return {"query": name, "normalized_key": key, "match_type": "exact", "persons": _person_dicts(tables, exact_positions)}

    tokens = key.split(" ")
    if len(tokens) < 2:
        message_note = "no exact match, and partial search needs at least two words"
        return {"query": name, "normalized_key": key, "match_type": "none", "persons": [], "note": message_note}

    partial_positions = _persons_with_all_words(tables, tokens)

    if len(partial_positions) == 0:
        return _relaxed_person_search(tables, name, key, tokens)

    if len(partial_positions) > MAX_CANDIDATES:
        return {"query": name, "normalized_key": key, "match_type": "too_many", "persons": [],
            "count": len(partial_positions),
            "note": f"more than {MAX_CANDIDATES} names contain all the query words"}

    return {"query": name, "normalized_key": key, "match_type": "partial", "persons": _person_dicts(tables, partial_positions),
        "note": "candidates that contain all the query words - not exact matches"}


def lookup_koststed(code: str) -> dict:
    """
    Looks up one koststed in report C, the authoritative catalog.

    Args:
        code (str): The koststed to look up, normally 8 digits.

    Returns:
        dict: status is "found", "not_in_catalog" or "invalid_format". Not in the catalog is not
        proof the code is invalid, it can be a retired code.
    """

    tables = get_tables()
    stripped_code = code.strip()

    if not _is_koststed(stripped_code):
        return {"query": stripped_code, "status": "invalid_format", "note": "a koststed is always exactly 8 digits"}

    if stripped_code not in tables.koststeder:
        return {"query": stripped_code, "status": "not_in_catalog",
            "note": "not in report C... Possibly a retired code, not proof it is invalid"}

    return {"query": stripped_code, "status": "found", "description": tables.koststeder[stripped_code]}


def lookup_ios_for_koststed(code: str) -> dict:
    """
    Lists every IO registered on one koststed in report B, ascending.

    For the manual search in the UI, not for the agent. One koststed can hold thousands of IOs,
    so the caller owns the scrolling.

    Args:
        code (str): The koststed, normally 8 digits.

    Returns:
        dict: status is "found", "no_ios" or "invalid_format", with the IOs and a count.
    """

    tables = get_tables()
    stripped_code = code.strip()

    if not _is_koststed(stripped_code):
        return {"query": stripped_code, "status": "invalid_format", "note": "a koststed is always exactly 8 digits"}

    ios = list(tables.ios_by_koststed.get(stripped_code, []))

    if len(ios) == 0:
        return {"query": stripped_code, "status": "no_ios", "count": 0, "ios": [],
            "note": "no IOs in report B are registered on this koststed"}

    return {"query": stripped_code, "status": "found", "count": len(ios), "ios": ios}


if __name__ == "__main__":

    started = time.perf_counter()
    tables = load_tables()
    load_seconds = time.perf_counter() - started

    counts = health()["table_counts"]
    assert counts["persons"] > 0 and counts["ios"] > 0 and counts["koststeder"] > 0, counts

    # round-trip the first row of each table through its lookup
    first_person = tables.persons[0]
    exact_result = lookup_person(first_person["name"])
    assert exact_result["match_type"] == "exact", exact_result["match_type"]
    found_names = []
    for person in exact_result["persons"]:
        found_names.append(person["name"])
    assert first_person["name"] in found_names

    first_io = next(iter(tables.ios))
    io_result = lookup_io(first_io)
    assert io_result["status"] in ("found", "found_unknown_koststed"), io_result["status"]
    if io_result["status"] == "found":
        assert io_result["koststed"] == tables.ios[first_io]["koststed"]

    first_koststed = next(iter(tables.koststeder))
    koststed_result = lookup_koststed(first_koststed)
    assert koststed_result["status"] == "found", koststed_result["status"]
    assert koststed_result["description"] == tables.koststeder[first_koststed]

    # the edge cases the API handled
    assert lookup_person("...")["match_type"] == "none"
    assert lookup_person("xq0w0")["match_type"] == "none"
    assert lookup_person("xq0w0 xq0w1")["match_type"] == "none"
    assert lookup_koststed("123")["status"] == "invalid_format"
    assert lookup_ios_for_koststed("123")["status"] == "invalid_format"
    assert lookup_io("600000000")["format_valid"] is True

    print(f"persons:    {counts['persons']} rows ({tables.skipped_names} unusable name rows skipped)")
    print(f"ios:        {counts['ios']} rows ({tables.unknown_ios} with unknown koststed)")
    print(f"koststeder: {counts['koststeder']} rows (skipped values: {tables.skipped_koststeder})")
    print(f"loaded in {load_seconds:.2f} s")
    print("lookup_data self-check passed")

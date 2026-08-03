


import os
import re
import sys
from pathlib import Path

import psycopg
import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI



# pointing to src folder for importing normalize file
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root / "src"))
load_dotenv(_repo_root / ".env.local")

from normalize import normalize_name


DATABASE_URL = os.environ["DATABASE_URL"]


app = FastAPI(title="UiT invoice lookup API")


def _query(sql: str, params: tuple) -> list:
    """
    Runs one query (read only) and returns all result rows.

    Args:
        sql (str): The SQL with %s placeholders.
        params (tuple): The values for the placeholders.

    Returns:
        list[tuple]: All rows from the query.
    """

    with psycopg.connect(DATABASE_URL) as connection:
        with connection.cursor() as cursor:
            cursor.execute(sql, params) # type: ignore
            rows = cursor.fetchall()
    return rows


def _person_dicts(rows: list) -> list:
    """
    Turns person result rows into response dicts.

    Args:
        rows (list[tuple]): Rows of (name_original, koststed, koststed_name).

    Returns:
        list[dict]: One dict per person.
    """

    # persons = []
    # for row in rows:
    #     persons.append({"name": row[0], "koststed": row[1], "koststed_name": row[2]})

    persons = [{"name": row[0], "koststed": row[1], "koststed_name": row[2]} for row in rows]
    return persons


@app.get("/health")
def health():
    """
    Checks that the service and the database are reachable

    Returns:
        dict: Status
    """

    counts = {}
    for table_name in ("persons", "ios", "koststeder"):
        rows = _query(f"SELECT COUNT(*) FROM {table_name}", ())
        counts[table_name] = rows[0][0]

    return {"status": "ok", "table_counts": counts}


@app.get("/lookup/io/{io}")
def lookup_io(io: str):
    """
    Looks up one IO (innkjøpsordrenummer) in report B.

    Args:
        io (str): The 9-digit IO to look up the number "6".

    Returns:
        dict: status is "found", "found_unknown_koststed" or  "not_found".
    """


    stripped_io = io.strip()
    format_valid = re.fullmatch(r"6\d{8}", stripped_io) is not None   # r"6\d{8} = starts with 6 followd by 8 digits"

    rows = _query("SELECT koststed, koststed_name, koststed_unknown FROM ios WHERE io = %s", (stripped_io,))

    if len(rows) == 0:
        message_note = "IO is not in report B, so it is unknown to Unit4"
        return {"query": stripped_io, "format_valid": format_valid, "status": "not_found", "note": message_note}

    koststed = rows[0][0]
    koststed_name = rows[0][1]
    koststed_unknown = rows[0][2]

    if koststed_unknown:
        message_note = "the IO exists but report B lists its koststed as unknown"
        return {"query": stripped_io, "format_valid": format_valid, "status": "found_unknown_koststed", "note": message_note}

    return {"query": stripped_io, "format_valid": format_valid, "status": "found", "koststed": koststed, "koststed_name": koststed_name}



@app.get("/lookup/person")
def lookup_person(name: str):
    """
    Looks up a person name in report A with two search methods.

    Args:
        name (str): The person name as written on the invoice.

    Returns:
        dict: match_type is "exact", "partial", "none" or "too_many".
    """

    key = normalize_name(name)

    # name is empty
    if key == "":
        message_note = "nothing left of the name after normalization"
        return {"query": name, "normalized_key": key, "match_type": "none", "persons": [], "note": message_note}



    # First search method where the whole normalized name must match
    exact_rows = _query("SELECT name_original, koststed, koststed_name FROM persons WHERE name_key = %s", (key,))
    if len(exact_rows) > 0:
        return {"query": name, "normalized_key": key, "match_type": "exact", "persons": _person_dicts(exact_rows)}


    # Second search where all query words must appear in the name in any order. Minimun 2 words...
    tokens = key.split(" ")
    if len(tokens) < 2:
        message_note = "no exact match, and partial search needs at least two words"
        return { "query": name, "normalized_key": key, "match_type": "none", "persons": [], "note": message_note}

    partial_rows = _query("SELECT name_original, koststed, koststed_name FROM persons WHERE name_tokens @> %s", (tokens,))

    if len(partial_rows) == 0:
        return {"query": name, "normalized_key": key, "match_type": "none", "persons": []}

    if len(partial_rows) > 10:
        return {"query": name, "normalized_key": key, "match_type": "too_many", "persons": [],
            "count": len(partial_rows),
            "note": f"more than {10} names contain all the query words"}



    return {"query": name, "normalized_key": key, "match_type": "partial", "persons": _person_dicts(partial_rows),
        "note": "candidates that contain all the query words - not exact matches"}



@app.get("/lookup/koststed/{code}")
def lookup_koststed(code: str):
    """
    Looks up one koststed in report C, the authoritative catalog.

    Args:
        code (str): The koststed to look up, normally 8 digits.

    Returns:
        dict: status is "found", "not_in_catalog" or "invalid_format". Not in the
        catalog is not proof the code is invalid, it can be a retired code.
    """

    stripped_code = code.strip()

    if re.fullmatch(r"\d{8}", stripped_code) is None:
        return {"query": stripped_code, "status": "invalid_format", "note": "a koststed is always exactly 8 digits"}

    rows = _query(
        "SELECT description FROM koststeder WHERE koststed = %s",
        (stripped_code,))

    if len(rows) == 0:
        return {"query": stripped_code, "status": "not_in_catalog",
            "note": "not in report C... Possibly a retired code, not proof it is invalid"}

    return {"query": stripped_code, "status": "found", "description": rows[0][0]}



if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)




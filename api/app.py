


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



def _relaxed_person_search(name: str, key: str, tokens: list):
    """
    Third search method where one query word may be wrong or extra.

    Retries the token search once per query word with that word left out.
    Needs at least 3 query words. A single hit can serve as the resolution;
    several hits are candidates for the caseworker.

    Args:
        name (str): The original query name.
        key (str): The normalized key of the query.
        tokens (list[str]): The words of the normalized key.

    Returns:
        dict: match_type is "partial_relaxed", "too_many" or "none".
    """

    if len(tokens) < 3:
        return {"query": name, "normalized_key": key, "match_type": "none", "persons": []}

    relaxed_rows = []
    seen_persons = []
    for drop_index in range(len(tokens)):
        reduced_tokens = tokens[:drop_index] + tokens[drop_index + 1:]
        subset_rows = _query(
            "SELECT name_original, koststed, koststed_name FROM persons WHERE name_tokens @> %s",
            (reduced_tokens,))

        # collect every hit once, even when several reduced queries find it
        for row in subset_rows:
            person_id = f"{row[0]}|{row[1]}"
            if person_id not in seen_persons:
                seen_persons.append(person_id)
                relaxed_rows.append(row)

    if len(relaxed_rows) == 0:
        return {"query": name, "normalized_key": key, "match_type": "none", "persons": []}

    if len(relaxed_rows) > 10:
        return {"query": name, "normalized_key": key, "match_type": "too_many", "persons": [],
            "count": len(relaxed_rows),
            "note": f"more than {10} names match all but one query word"}

    return {"query": name, "normalized_key": key, "match_type": "partial_relaxed",
        "persons": _person_dicts(relaxed_rows),
        "note": "these names match all but one query word"}


@app.get("/lookup/person")
def lookup_person(name: str):
    """
    Looks up a person name in report A with three search methods.

    Args:
        name (str): The person name as written on the invoice.

    Returns:
        dict: match_type is "exact", "partial", "partial_relaxed", "none"
        or "too_many".
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
        return _relaxed_person_search(name, key, tokens)

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



@app.get("/lookup/koststed/{code}/ios")
def lookup_ios_for_koststed(code: str):
    """
    Lists every IO registered on one koststed in report B.

    For the manual search in the demo, not for the agent - the agent's
    tool registry does not include this. The list can be long (one
    koststed can hold thousands of IOs), so the caller owns the scrolling.

    Args:
        code (str): The koststed, normally 8 digits.

    Returns:
        dict: status is "found", "no_ios" or "invalid_format", with the
        IOs sorted ascending and a count.
    """

    stripped_code = code.strip()

    if re.fullmatch(r"\d{8}", stripped_code) is None:
        return {"query": stripped_code, "status": "invalid_format", "note": "a koststed is always exactly 8 digits"}

    rows = _query("SELECT io FROM ios WHERE koststed = %s ORDER BY io", (stripped_code,))

    ios = []
    for row in rows:
        ios.append(row[0])

    if len(ios) == 0:
        return {"query": stripped_code, "status": "no_ios", "count": 0, "ios": [],
            "note": "no IOs in report B are registered on this koststed"}

    return {"query": stripped_code, "status": "found", "count": len(ios), "ios": ios}



if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8000)




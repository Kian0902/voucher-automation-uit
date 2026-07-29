
import json
import re
from datetime import date
from pathlib import Path

from anyascii import anyascii


# warnning when the index is expired...
STALE_AFTER_DAYS = 30


def _name_key(name):
    """
    Reduce a name to a form that matches regardless of word order or letters.

    Both "Firstname, Surname" and "FIRSTNAME SURNAME" become "firstname surname".
    Letters outside plain ASCII (som f.eks, æ, ø og å) are transliterated.
    Args:
        name (str | None): The name as written.

    Returns:
        str: The normalized key, or "" if the name holds no letters.

    Example:
        "Firstname, Surname"  -> "andersen sigmund"
        "FIRSTNAME  SURNAME"  -> "andersen sigmund"
        "Berg, Bjørn"        -> "berg bjorn"
        "Bjorn Berg"         -> "berg bjorn"
    """

    if name is None:
        return ""

    words = re.findall(r"\w+", anyascii(name).lower())
    return " ".join(sorted(words))


_json_path = Path(__file__).resolve().parent.parent / "data" / "employees.json"

with open(_json_path, encoding="utf-8") as json_file:
    _index = json.load(json_file)

EMPLOYEES = _index["employees"]
UNITS = _index["units"]
FETCHED = _index["fetched"]

AGE_IN_DAYS = (date.today() - date.fromisoformat(FETCHED)).days
IS_STALE = AGE_IN_DAYS > STALE_AFTER_DAYS

if IS_STALE:
    print(f"WARNING: the employee index is {AGE_IN_DAYS} days old (fetched {FETCHED}). Rebuild it with scripts/scrape_employees.py.")


BY_EMAIL = {}
for _employee in EMPLOYEES:
    if _employee["email"] is not None:
        BY_EMAIL[_employee["email"].strip().lower()] = _employee

# People can share a name so every key holds a list
BY_NAME = {}
for _employee in EMPLOYEES:
    _key = _name_key(_employee["name"])
    if _key != "":
        BY_NAME.setdefault(_key, []).append(_employee)


def find_by_email(email):
    """
    Find the employee owning an email address.

    Args:
        email (str | None): The address, with or without a "mailto:" prefix.

    Returns:
        dict | None: The employee record, or None if no one owns it.
    """
    if email is None:
        return None

    cleaned = email.strip().lower()
    cleaned = cleaned.removeprefix("mailto:").strip()

    return BY_EMAIL.get(cleaned)


def find_by_name(name):
    """
    Find every employee whose name matches, in any word order.

    Args:
        name (str | None): The name as written on the invoice.

    Returns:
        list[dict]: Every matching employee record.
    Example:
        find_by_name("Fornavn Etternavn")            -> [ {...} ]
        find_by_name("Fornavn Mellomnavn Etternavn") -> [ {...} ]
        find_by_name("Nobody Here")                  -> []
    """


    key = _name_key(name)
    if key == "":
        return []

    exact_matches = BY_NAME.get(key)
    if exact_matches is not None:

        return list(exact_matches)

    # for no full match, accept records that contain all the words given
    wanted_words = set(key.split(" "))
    partial_matches = []

    for employee in EMPLOYEES:
        employee_words = set(_name_key(employee["name"]).split(" "))

        if wanted_words.issubset(employee_words):
            partial_matches.append(employee)
            
    return partial_matches


if __name__ == "__main__":
    ...


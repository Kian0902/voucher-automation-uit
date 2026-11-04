"""
Run records and the dashboard built on them.

Every run, finished or failed, appends one JSON line to RECORDS_FILE: when it ended, what kind
of file it was, how long it took, which model answered, and one item per invoice with its
result, the reasons it was not resolved, its time, tokens and lookups. A row of the ERP list is
one item, an XML invoice is one item. The dashboard endpoint reads that file only, never the
per-run outputs, so it stays fast and counts invoice runs too.

server.py sets RECORDS_FILE and REPORTS_DIR after the import, the way it sets run_log.LOGS_DIR.
"""

import json
import statistics
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

from fastapi import APIRouter

# src/ is on the path when server.py imports this module; the self-check below needs it too
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import lookup_data
from erp_list import RESULT_LABELS


# set by server.py to the folders next to the app
RECORDS_FILE = Path("output") / "runs.jsonl"
REPORTS_DIR = lookup_data.REPORTS_DIR

DEFAULT_WEEKS = 8
MAX_WEEKS = 52
RECENT_RUNS = 10

# Norwegian names for the reason codes the row agent records, shown on the dashboard
REASON_NAMES = {
    "ingen_verdi": "Ingen verdi å slå opp",
    "ingen_konklusjon": "Modellen ga ingen konklusjon",
    "io_ukjent": "IO ukjent for Unit4",
    "io_uten_koststed": "IO uten koststed i rapport B",
    "navn_ikke_funnet": "Navn ikke funnet i rapport A",
    "navn_for_vanlig": "Navnet er for vanlig",
    "koststed_ikke_i_katalog": "Koststed ikke i katalogen",
    "oppslag_feilet": "Et oppslag feilet",
    "annet": "Annet",
}

# the three reports as the dashboard names them, with the table each one fills
REPORTS = [
    ("Rapport A, personer", lookup_data.REPORT_A.name, "persons"),
    ("Rapport B, innkjøpsordrer", lookup_data.REPORT_B.name, "ios"),
    ("Rapport C, koststeder", lookup_data.REPORT_C.name, "koststeder"),
]

router = APIRouter()


# RECORDS

def new_item(name: str, resultat, grunner: list, seconds, tokens, lookups) -> dict:
    """
    One invoice inside a run record.

    Args:
        name (str): "rad 3" for a row of the list, "faktura" for an XML invoice.
        resultat (str | None): One of RESULT_LABELS, or None when the agent gave no result.
        grunner (list[str]): The reason codes for a row that is not løst, empty otherwise.
        seconds (int | float | None): Time the agent spent on it.
        tokens (int | None): Prompt plus generated tokens over all its model calls.
        lookups (int | None): How many lookups the agent made for it.

    Returns:
        dict: The item as stored in the record.
    """

    item = {
        "item": name,
        "resultat": resultat,
        "grunner": grunner,
        "seconds": seconds,
        "tokens": tokens,
        "lookups": lookups,
    }
    return item


def new_record(kind: str, filename: str, status: str, error, duration_seconds, model, items: list) -> dict:
    """
    Build the record of one run, with the result counts and totals summed over its items.

    Args:
        kind (str): "faktura" or "liste".
        filename (str): The uploaded file's name.
        status (str): "done" or "error".
        error (str | None): The error text of a failed run.
        duration_seconds (float | None): Wall time of the run, None when it failed.
        model (str | None): The model that answered, None when no model call completed.
        items (list[dict]): One new_item() per invoice the run handled.

    Returns:
        dict: The record, finished_at set to now.
    """

    results = {}
    for label in RESULT_LABELS:
        results[label] = 0
    tokens = 0
    lookups = 0
    for item in items:
        if item["resultat"] in results:
            results[item["resultat"]] = results[item["resultat"]] + 1
        if item["tokens"] is not None:
            tokens = tokens + item["tokens"]
        if item["lookups"] is not None:
            lookups = lookups + item["lookups"]

    record = {
        "finished_at": datetime.now().isoformat(timespec="seconds"),
        "kind": kind,
        "filename": filename,
        "status": status,
        "error": error,
        "duration_seconds": duration_seconds,
        "model": model,
        "items": items,
        "results": results,
        "tokens": tokens,
        "lookups": lookups,
    }
    return record


def append_record(record: dict) -> None:
    """
    Append one record as a line to RECORDS_FILE, creating the folder and the file as needed.

    Args:
        record (dict): A record from new_record().
    """

    RECORDS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with RECORDS_FILE.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_records() -> list:
    """
    Every record in file order. A line that is not JSON is skipped, so one broken write never
    hides the rest.

    Returns:
        list[dict]: The records, oldest first; empty when the file does not exist.
    """

    if not RECORDS_FILE.is_file():
        return []
    records = []
    for line in RECORDS_FILE.read_text(encoding="utf-8").splitlines():
        if line.strip() == "":
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        records.append(record)
    return records


# AGGREGATES

def week_start(day: date) -> date:
    """
    The Monday of the week a day belongs to.

    Args:
        day (date): Any day.

    Returns:
        date: The Monday on or before it.

    Example:
        week_start(date(2026, 11, 4)) == date(2026, 11, 2)
    """

    return day - timedelta(days=day.weekday())


def _average_and_median(values: list) -> dict:
    """
    Average and median of a list of numbers, None for both when the list is empty.

    Args:
        values (list): Numbers.

    Returns:
        dict: count, average and median; the two rounded to one decimal.
    """

    if len(values) == 0:
        return {"count": 0, "average": None, "median": None}
    summary = {
        "count": len(values),
        "average": round(statistics.mean(values), 1),
        "median": round(statistics.median(values), 1),
    }
    return summary


def _empty_week(start: date) -> dict:
    """
    The counters of one week on the chart, all zero.

    Args:
        start (date): The week's Monday.

    Returns:
        dict: week_start, runs, items and one counter per result label.
    """

    week = {"week_start": start.isoformat(), "runs": 0, "items": 0}
    for label in RESULT_LABELS:
        week[label] = 0
    return week


def aggregate(records: list, weeks: int, today: date) -> dict:
    """
    The dashboard numbers for the last weeks, this week included.

    Args:
        records (list[dict]): The records from read_records().
        weeks (int): How many weeks back, counting the current one.
        today (date): The day the window ends in.

    Returns:
        dict: weeks, per_week for the chart (oldest first), this_week, totals over the window,
              time per invoice (all, liste, faktura), tokens, lookups, reasons for uløst, models
              and the recent runs over all time (newest first).
    """

    first_start = week_start(today) - timedelta(weeks=weeks - 1)
    per_week = []
    week_by_start = {}
    for offset in range(weeks):
        start = first_start + timedelta(weeks=offset)
        week = _empty_week(start)
        per_week.append(week)
        week_by_start[week["week_start"]] = week

    runs = 0
    failed_runs = 0
    items = 0
    tokens = 0
    lookups = 0
    seconds_by_kind = {"liste": [], "faktura": []}
    reason_counts = {}
    model_counts = {}

    for record in records:
        finished = datetime.fromisoformat(record["finished_at"]).date()
        week = week_by_start.get(week_start(finished).isoformat())
        if week is None:
            # before the window, or dated after today by a clock change
            continue

        runs = runs + 1
        week["runs"] = week["runs"] + 1
        if record["status"] == "error":
            failed_runs = failed_runs + 1
        tokens = tokens + record["tokens"]
        lookups = lookups + record["lookups"]
        if record["model"] is not None:
            model_counts[record["model"]] = model_counts.get(record["model"], 0) + 1

        for item in record["items"]:
            items = items + 1
            week["items"] = week["items"] + 1
            label = item["resultat"]
            if label in RESULT_LABELS:
                week[label] = week[label] + 1
            if item["seconds"] is not None:
                seconds_by_kind[record["kind"]].append(item["seconds"])
            if label == "uløst":
                for code in item["grunner"]:
                    reason_counts[code] = reason_counts.get(code, 0) + 1

    totals = {"runs": runs, "failed_runs": failed_runs, "items": items}
    for label in RESULT_LABELS:
        totals[label] = 0
        for week in per_week:
            totals[label] = totals[label] + week[label]

    reasons = []
    for code, count in sorted(reason_counts.items(), key=lambda pair: pair[1], reverse=True):
        reasons.append({"code": code, "name": REASON_NAMES.get(code, code), "count": count})

    models = []
    for model, count in sorted(model_counts.items(), key=lambda pair: pair[1], reverse=True):
        models.append({"model": model, "runs": count})

    recent = []
    for record in reversed(records[-RECENT_RUNS:]):
        recent.append({
            "finished_at": record["finished_at"],
            "kind": record["kind"],
            "filename": record["filename"],
            "status": record["status"],
            "error": record["error"],
            "items": len(record["items"]),
            "results": record["results"],
            "duration_seconds": record["duration_seconds"],
            "tokens": record["tokens"],
        })

    per_item = {"tokens": None, "lookups": None}
    if items > 0:
        per_item["tokens"] = round(tokens / items)
        per_item["lookups"] = round(lookups / items, 1)

    summary = {
        "weeks": weeks,
        "per_week": per_week,
        "this_week": per_week[-1],
        "totals": totals,
        "time": {
            "all": _average_and_median(seconds_by_kind["liste"] + seconds_by_kind["faktura"]),
            "liste": _average_and_median(seconds_by_kind["liste"]),
            "faktura": _average_and_median(seconds_by_kind["faktura"]),
        },
        "tokens": tokens,
        "lookups": lookups,
        "per_item": per_item,
        "reasons": reasons,
        "models": models,
        "recent": recent,
    }
    return summary


def report_status() -> list:
    """
    Name, file, row count and file date of each report, for the freshness box.

    Returns:
        list[dict]: One entry per report; rows is None when the tables are not loaded, and
                    modified is None when the file is missing.
    """

    counts = {}
    try:
        counts = lookup_data.health()["table_counts"]
    except (FileNotFoundError, ValueError):
        pass

    entries = []
    for title, file_name, table in REPORTS:
        path = REPORTS_DIR / file_name
        modified = None
        if path.is_file():
            modified = datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
        entries.append({"title": title, "file": file_name, "rows": counts.get(table), "modified": modified})
    return entries


# ROUTE

@router.get("/dashboard")
def dashboard(weeks: int = DEFAULT_WEEKS) -> dict:
    """
    The dashboard numbers for the last weeks plus the state of the reports.

    Args:
        weeks (int): How many weeks back, 1 to MAX_WEEKS, the current week included.

    Returns:
        dict: aggregate() of the records, with reports added.
    """

    weeks = max(1, min(weeks, MAX_WEEKS))
    summary = aggregate(read_records(), weeks, date.today())
    summary["reports"] = report_status()
    return summary


if __name__ == "__main__":
    import tempfile

    assert week_start(date(2026, 11, 4)) == date(2026, 11, 2), "Wednesday maps to its Monday"
    assert week_start(date(2026, 11, 2)) == date(2026, 11, 2), "Monday maps to itself"

    with tempfile.TemporaryDirectory() as folder:
        RECORDS_FILE = Path(folder) / "runs.jsonl"
        assert read_records() == [], "no file means no records"

        rows = [
            new_item("rad 1", "løst", [], 2, 1200, 2),
            new_item("rad 2", "uløst", ["io_ukjent"], 3, 900, 1),
            new_item("rad 3", "uløst", ["ingen_verdi"], 0, 0, 0),
            new_item("rad 4", "må bekreftes", ["navn_ikke_funnet"], 5, 2000, 2),
        ]
        list_record = new_record("liste", "liste.png", "done", None, 41.3, "test-model", rows)
        assert list_record["results"] == {"løst": 1, "må bekreftes": 1, "flere funnet": 0, "uløst": 2}
        assert list_record["tokens"] == 4100 and list_record["lookups"] == 5
        append_record(list_record)

        invoice_record = new_record("faktura", "f.xml", "done", None, 12.0, "test-model",
                                    [new_item("faktura", "løst", [], 12.0, 3000, 3)])
        append_record(invoice_record)
        failed_record = new_record("liste", "x.png", "error", "Tabellen kunne ikke tolkes", None, None, [])
        append_record(failed_record)

        # a broken line in the file is skipped, not fatal
        with RECORDS_FILE.open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        records = read_records()
        assert len(records) == 3, f"three records expected, got {len(records)}"

        today = datetime.fromisoformat(records[0]["finished_at"]).date()
        summary = aggregate(records, 4, today)
        assert len(summary["per_week"]) == 4
        assert summary["this_week"]["items"] == 5 and summary["this_week"]["løst"] == 2
        assert summary["totals"] == {"runs": 3, "failed_runs": 1, "items": 5,
                                     "løst": 2, "må bekreftes": 1, "flere funnet": 0, "uløst": 2}
        assert summary["time"]["liste"]["median"] == 2.5 and summary["time"]["faktura"]["average"] == 12.0
        assert summary["tokens"] == 7100 and summary["per_item"]["tokens"] == 1420
        assert summary["reasons"][0]["count"] == 1 and len(summary["reasons"]) == 2
        assert summary["models"] == [{"model": "test-model", "runs": 2}]
        assert summary["recent"][0]["status"] == "error" and len(summary["recent"]) == 3

        # a record before the window is left out
        old_record = dict(list_record)
        old_record["finished_at"] = (today - timedelta(weeks=6)).isoformat() + "T10:00:00"
        summary = aggregate(records + [old_record], 4, today)
        assert summary["totals"]["runs"] == 3, "the old record must not count"

    print("api_dashboard: all checks passed")

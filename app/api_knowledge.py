"""
The knowledge base: the three report CSVs read, edited, replaced and downloaded from the app.

The files next to the app are the source of truth, so every change goes to disk first: the rows
are written to a temporary file, checked with the loader in src/lookup_data.py, the previous
file is kept as one backup in data/koststed_tables/backup/, the new file is moved into place and
the tables are reloaded. Writes are refused while a run is in progress, since the agents read
the tables. One user, one lock, one write at a time.
"""

import csv
import os
import re
import shutil
import sys
import threading
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, JSONResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import lookup_data
from lookup_data import HEADER_A, HEADER_B, HEADER_C, REPORT_A, REPORT_B, REPORT_C


# the folder that holds the three files, set by server.py to the one next to the app
REPORTS_DIR = lookup_data.REPORTS_DIR
BACKUP_DIR_NAME = "backup"
PAGE_SIZES = [25, 50, 100, 200]
DEFAULT_PAGE_SIZE = 50

IO_PATTERN = re.compile(r"6\d{8}")
KOSTSTED_PATTERN = re.compile(r"\d{8}")

# the reports as the page shows them: the file, its columns, the key of its loaded count in
# health() and the column that must be unique (None for report A, where a name may recur)
REPORTS = {
    "a": {
        "title": "Rapport A, personer",
        "file": REPORT_A.name,
        "header": HEADER_A,
        "count_key": "persons",
        "key_column": None,
    },
    "b": {
        "title": "Rapport B, innkjøpsordrer",
        "file": REPORT_B.name,
        "header": HEADER_B,
        "count_key": "ios",
        "key_column": 0,
    },
    "c": {
        "title": "Rapport C, koststeder",
        "file": REPORT_C.name,
        "header": HEADER_C,
        "count_key": "koststeder",
        "key_column": 0,
    },
}

WRITE_LOCK = threading.Lock()
# report id -> {"mtime": float, "rows": list[list[str]]}, so a page of rows does not reread the file
_CACHE = {}

router = APIRouter()


# HOOKS, replaced by server.py

def run_is_active() -> bool:
    """
    Whether an agent run is in progress right now.

    server.py replaces this function with a check of its run state, so a write is refused
    while the agents read the tables. On its own the module never sees a run.

    Returns:
        bool: False.
    """

    return False


def reload_tables():
    """
    Reload the lookup tables from REPORTS_DIR after a file changed.

    server.py replaces this function with its own reload, which also sets or clears the data
    error every page shows.

    Returns:
        str | None: The error text when the reload failed, else None.
    """

    try:
        lookup_data.load_tables(REPORTS_DIR)
    except (FileNotFoundError, ValueError) as error:
        return str(error)
    return None


# READING

def report_path(report_id: str) -> Path:
    """
    The file of one report.

    Args:
        report_id (str): "a", "b" or "c".

    Returns:
        Path: The CSV in REPORTS_DIR.
    """

    return REPORTS_DIR / REPORTS[report_id]["file"]


def read_report(report_id: str) -> list:
    """
    The data rows of one report, from the cache when the file has not changed since.

    Args:
        report_id (str): "a", "b" or "c".

    Returns:
        list[list[str]]: The rows as they stand in the file, header excluded.

    Raises:
        FileNotFoundError: When the file is missing.
        ValueError: When the header differs from the expected layout.
    """

    path = report_path(report_id)
    mtime = path.stat().st_mtime
    cached = _CACHE.get(report_id)
    if cached is not None and cached["mtime"] == mtime:
        return cached["rows"]

    rows = lookup_data.read_rows(path, REPORTS[report_id]["header"])
    _CACHE[report_id] = {"mtime": mtime, "rows": rows}
    return rows


def filter_rows(rows: list, query: str) -> list:
    """
    The rows that contain every word of the query, case-insensitive, in file order.

    Args:
        rows (list[list[str]]): The data rows of a report.
        query (str): What was typed in the search field; blank keeps every row.

    Returns:
        list[tuple[int, list[str]]]: (position, row) pairs.
    """

    words = query.lower().split()
    matches = []
    for index, row in enumerate(rows):
        if len(words) == 0:
            matches.append((index, row))
            continue
        text = " ".join(row).lower()
        all_found = True
        for word in words:
            if word not in text:
                all_found = False
                break
        if all_found:
            matches.append((index, row))
    return matches


# CHECKING

def clean_values(values: list) -> list:
    """
    The values as they are stored: strings without surrounding spaces.

    Args:
        values (list): What the page sent.

    Returns:
        list[str]: One stripped string per value.
    """

    cleaned = []
    for value in values:
        cleaned.append(str(value).strip())
    return cleaned


def validate_values(report_id: str, values: list) -> str | None:
    """
    Checks one row before it is written, by the format rules of the report.

    Report A needs a name and an 8-digit koststed, B an IO (9 digits, starting with 6) and an
    8-digit koststed, C an 8-digit koststed and a description. The exports also hold rows the
    loader skips or marks unknown, such as "Ukjent" koststeder; those stay as they are, but a
    row made in the app must be usable.

    Args:
        report_id (str): "a", "b" or "c".
        values (list[str]): The cleaned values, one per column.

    Returns:
        str | None: The Norwegian error text, or None when the row is fine.
    """

    header = REPORTS[report_id]["header"]
    if len(values) != len(header):
        return f"Raden må ha {len(header)} verdier"

    if report_id == "a":
        if values[0] == "":
            return "Navn kan ikke være tomt"
        if KOSTSTED_PATTERN.fullmatch(values[1]) is None:
            return "Koststedsnr. må være 8 sifre"
    elif report_id == "b":
        if IO_PATTERN.fullmatch(values[0]) is None:
            return "Innkjøpsordrenr. må være 9 sifre og starte med 6"
        if KOSTSTED_PATTERN.fullmatch(values[1]) is None:
            return "Koststedsnr. må være 8 sifre"
    else:
        if KOSTSTED_PATTERN.fullmatch(values[0]) is None:
            return "Koststedsnr. må være 8 sifre"
        if values[1] == "":
            return "Koststedsnavn kan ikke være tomt"
    return None


def find_conflict(report_id: str, rows: list, values: list, skip_index) -> str | None:
    """
    The row the new values would collide with: the same IO in B, the same koststed in C, or
    an identical row in A.

    Args:
        report_id (str): "a", "b" or "c".
        rows (list[list[str]]): The current data rows.
        values (list[str]): The cleaned values about to be written.
        skip_index (int | None): The position of the row being edited, which may keep its key.

    Returns:
        str | None: Norwegian error text naming the row, or None.
    """

    report = REPORTS[report_id]
    key_column = report["key_column"]
    for index, row in enumerate(rows):
        if index == skip_index:
            continue
        existing = clean_values(row)
        if key_column is None:
            if existing == values:
                return f"Rad {index + 1} er allerede identisk"
        else:
            if existing[key_column] == values[key_column]:
                column_name = report["header"][key_column]
                return f"Rad {index + 1} har allerede {column_name} {values[key_column]}"
    return None


def row_is_stale(rows: list, index, old_values: list) -> bool:
    """
    Whether the row the page wants to change is no longer what the page showed.

    Args:
        rows (list[list[str]]): The current data rows.
        index: The position the page sent, any type.
        old_values (list[str]): The cleaned values the page showed for that row.

    Returns:
        bool: True when the position is invalid or the row differs.
    """

    if not isinstance(index, int) or index < 0 or index >= len(rows):
        return True
    return clean_values(rows[index]) != old_values


# WRITING

def _install(report_id: str, new_path: Path) -> None:
    """
    Puts a checked file in place of the report: one backup of the current file, then an
    atomic replace. The cache forgets the report so the next read sees the new file.

    Args:
        report_id (str): "a", "b" or "c".
        new_path (Path): The checked file, in REPORTS_DIR.
    """

    target = report_path(report_id)
    if target.exists():
        backup_dir = REPORTS_DIR / BACKUP_DIR_NAME
        backup_dir.mkdir(exist_ok=True)
        shutil.copy2(target, backup_dir / REPORTS[report_id]["file"])
    os.replace(new_path, target)
    _CACHE.pop(report_id, None)


def write_rows(report_id: str, rows: list) -> None:
    """
    Writes the rows as the report file: header plus rows, UTF-8 with BOM and CRLF like the
    exports, checked with the loader before it replaces the current file.

    Args:
        report_id (str): "a", "b" or "c".
        rows (list[list[str]]): Every data row the file should hold.

    Raises:
        ValueError: When the written file fails the loader's check; the current file is untouched.
    """

    report = REPORTS[report_id]
    temp_path = REPORTS_DIR / (report["file"] + ".tmp")
    with open(temp_path, "w", encoding="utf-8-sig", newline="") as temp_file:
        writer = csv.writer(temp_file)
        writer.writerow(report["header"])
        writer.writerows(rows)

    try:
        lookup_data.read_rows(temp_path, report["header"])
    except ValueError:
        temp_path.unlink()
        raise
    _install(report_id, temp_path)


def install_upload(report_id: str, file_bytes: bytes) -> int:
    """
    Checks an uploaded export and puts it in place of the report.

    Args:
        report_id (str): "a", "b" or "c".
        file_bytes (bytes): The uploaded file.

    Returns:
        int: The number of data rows in the new file.

    Raises:
        ValueError: Norwegian text when the file is not UTF-8, not CSV or has other columns;
                    the current file is untouched.
    """

    report = REPORTS[report_id]
    temp_path = REPORTS_DIR / (report["file"] + ".tmp")
    temp_path.write_bytes(file_bytes)

    try:
        rows = lookup_data.read_rows(temp_path, report["header"])
    except UnicodeDecodeError:
        temp_path.unlink()
        raise ValueError("Filen er ikke lagret som UTF-8. Eksporter den på nytt som CSV UTF-8")
    except csv.Error as error:
        temp_path.unlink()
        raise ValueError(f"Filen kan ikke leses som CSV: {error}")
    except ValueError as error:
        temp_path.unlink()
        expected = ", ".join(report["header"])
        raise ValueError(
            f"Filen må ha kolonnene {expected} i den rekkefølgen, med komma som skilletegn. "
            f"Leseren sa: {error}"
        )

    _install(report_id, temp_path)
    return len(rows)


def _finish_write(report_id: str) -> str | None:
    """
    Reloads the tables after a file changed. When the reload fails, the backup is put back
    and loaded again, so the app keeps working on the previous file.

    Args:
        report_id (str): The report whose file changed.

    Returns:
        str | None: Norwegian error text when the new file could not be loaded, else None.
    """

    error = reload_tables()
    if error is None:
        return None

    backup_path = REPORTS_DIR / BACKUP_DIR_NAME / REPORTS[report_id]["file"]
    if backup_path.exists():
        shutil.copy2(backup_path, report_path(report_id))
        _CACHE.pop(report_id, None)
        reload_tables()
    return f"Den nye filen kunne ikke lastes, forrige fil er satt tilbake: {error}"


def _refuse_write(report_id: str):
    """
    The answer a write gets when it cannot happen now.

    Args:
        report_id (str): What the page sent.

    Returns:
        JSONResponse | None: 404 for an unknown report, 409 while a run is in progress, else None.
    """

    if report_id not in REPORTS:
        return JSONResponse({"message": "Ukjent rapport"}, status_code=404)
    if run_is_active():
        return JSONResponse(
            {"message": "En kjøring pågår. Vent til den er ferdig før du endrer rapportene"},
            status_code=409,
        )
    return None


def _current_rows_or_error(report_id: str):
    """
    The current rows of a report, or the answer when the file cannot be read.

    Args:
        report_id (str): "a", "b" or "c".

    Returns:
        tuple[list | None, JSONResponse | None]: The rows, or a 503 answer.
    """

    try:
        rows = read_report(report_id)
    except (FileNotFoundError, ValueError, csv.Error) as error:
        return None, JSONResponse({"message": f"Rapporten kan ikke leses: {error}"}, status_code=503)
    return rows, None


def _write_and_reload(report_id: str, new_rows: list):
    """
    Writes the rows and reloads the tables; the caller holds WRITE_LOCK.

    Args:
        report_id (str): "a", "b" or "c".
        new_rows (list[list[str]]): Every data row the file should hold.

    Returns:
        JSONResponse | None: A 500 answer when the write or the reload failed, else None.
    """

    try:
        write_rows(report_id, new_rows)
    except (ValueError, OSError) as error:
        return JSONResponse({"message": f"Filen kunne ikke skrives: {error}"}, status_code=500)
    reload_error = _finish_write(report_id)
    if reload_error is not None:
        return JSONResponse({"message": reload_error}, status_code=500)
    return None


# ENDPOINTS

@router.get("/knowledge/reports")
def reports() -> dict:
    """
    The three reports with their columns, file row counts, loaded counts and file dates.

    Returns:
        dict: reports (list), locked (a run is in progress), page_sizes and the backup folder.
    """

    loaded_counts = {}
    try:
        loaded_counts = lookup_data.health()["table_counts"]
    except (FileNotFoundError, ValueError):
        pass  # not loaded; the page shows the file counts only

    items = []
    for report_id, report in REPORTS.items():
        path = report_path(report_id)
        item = {
            "id": report_id,
            "title": report["title"],
            "file": report["file"],
            "columns": report["header"],
            "rows": None,
            "loaded": loaded_counts.get(report["count_key"]),
            "modified": None,
            "error": None,
        }
        try:
            item["rows"] = len(read_report(report_id))
        except (FileNotFoundError, ValueError, csv.Error) as error:
            item["error"] = str(error)
        if path.exists():
            modified = datetime.fromtimestamp(path.stat().st_mtime)
            item["modified"] = modified.isoformat(timespec="seconds")
        items.append(item)

    return {
        "reports": items,
        "locked": run_is_active(),
        "page_sizes": PAGE_SIZES,
        "backup_folder": f"data/{REPORTS_DIR.name}/{BACKUP_DIR_NAME}",
    }


@router.get("/knowledge/rows")
def rows(report: str, query: str = "", page: int = 1, size: int = DEFAULT_PAGE_SIZE):
    """
    One page of a report's rows, filtered by the search words.

    Args:
        report (str): "a", "b" or "c".
        query (str): Search words; every word must occur somewhere in the row.
        page (int): 1-based page number, clamped to the pages that exist.
        size (int): Rows per page, one of PAGE_SIZES.

    Returns:
        dict: total, page, pages, size and rows as [{"index", "values"}], index being the
              row's position in the file (0-based, header excluded).
    """

    if report not in REPORTS:
        return JSONResponse({"message": "Ukjent rapport"}, status_code=404)
    all_rows, error = _current_rows_or_error(report)
    if error is not None:
        return error

    matches = filter_rows(all_rows, query)
    if size not in PAGE_SIZES:
        size = DEFAULT_PAGE_SIZE
    page_count = max(1, (len(matches) + size - 1) // size)
    page = min(max(page, 1), page_count)
    start = (page - 1) * size

    page_rows = []
    for index, row in matches[start:start + size]:
        page_rows.append({"index": index, "values": row})
    return {
        "report": report,
        "query": query,
        "total": len(matches),
        "page": page,
        "pages": page_count,
        "size": size,
        "rows": page_rows,
    }


@router.post("/knowledge/add")
def add_row(payload: dict):
    """
    Appends one row to a report.

    Args:
        payload (dict): {"report": "a" | "b" | "c", "values": [one per column]}.

    Returns:
        dict with message and the new row's index, or an error answer.
    """

    report_id = str(payload.get("report", ""))
    refusal = _refuse_write(report_id)
    if refusal is not None:
        return refusal

    values = clean_values(payload.get("values", []))
    problem = validate_values(report_id, values)
    if problem is not None:
        return JSONResponse({"message": problem}, status_code=400)

    with WRITE_LOCK:
        current_rows, error = _current_rows_or_error(report_id)
        if error is not None:
            return error
        conflict = find_conflict(report_id, current_rows, values, None)
        if conflict is not None:
            return JSONResponse({"message": conflict}, status_code=409)
        new_rows = current_rows + [values]
        error = _write_and_reload(report_id, new_rows)
        if error is not None:
            return error

    return {"message": f"Raden er lagt til som rad {len(new_rows)}", "index": len(new_rows) - 1}


@router.post("/knowledge/edit")
def edit_row(payload: dict):
    """
    Replaces the values of one row.

    Args:
        payload (dict): {"report", "index", "old_values" (what the page showed), "values"}.

    Returns:
        dict with message, or an error answer; 409 when the row changed since the page loaded.
    """

    report_id = str(payload.get("report", ""))
    refusal = _refuse_write(report_id)
    if refusal is not None:
        return refusal

    index = payload.get("index")
    values = clean_values(payload.get("values", []))
    old_values = clean_values(payload.get("old_values", []))
    problem = validate_values(report_id, values)
    if problem is not None:
        return JSONResponse({"message": problem}, status_code=400)

    with WRITE_LOCK:
        current_rows, error = _current_rows_or_error(report_id)
        if error is not None:
            return error
        if row_is_stale(current_rows, index, old_values):
            return JSONResponse(
                {"message": "Raden er endret siden siden ble lastet. Last rapporten på nytt"},
                status_code=409,
            )
        conflict = find_conflict(report_id, current_rows, values, index)
        if conflict is not None:
            return JSONResponse({"message": conflict}, status_code=409)
        new_rows = list(current_rows)
        new_rows[index] = values
        error = _write_and_reload(report_id, new_rows)
        if error is not None:
            return error

    return {"message": f"Rad {index + 1} er lagret"}


@router.post("/knowledge/delete")
def delete_row(payload: dict):
    """
    Removes one row.

    Args:
        payload (dict): {"report", "index", "old_values" (what the page showed)}.

    Returns:
        dict with message, or an error answer; 409 when the row changed since the page loaded.
    """

    report_id = str(payload.get("report", ""))
    refusal = _refuse_write(report_id)
    if refusal is not None:
        return refusal

    index = payload.get("index")
    old_values = clean_values(payload.get("old_values", []))

    with WRITE_LOCK:
        current_rows, error = _current_rows_or_error(report_id)
        if error is not None:
            return error
        if row_is_stale(current_rows, index, old_values):
            return JSONResponse(
                {"message": "Raden er endret siden siden ble lastet. Last rapporten på nytt"},
                status_code=409,
            )
        if len(current_rows) == 1:
            return JSONResponse({"message": "Rapporten kan ikke tømmes helt"}, status_code=400)
        new_rows = current_rows[:index] + current_rows[index + 1:]
        error = _write_and_reload(report_id, new_rows)
        if error is not None:
            return error

    return {"message": f"Rad {index + 1} er slettet"}


@router.post("/knowledge/upload")
async def upload(request: Request, report: str, filename: str = "fil"):
    """
    Replaces a report with an uploaded export, after the loader's check.

    Args:
        request (Request): The request whose body is the raw file.
        report (str): "a", "b" or "c", from the query string.
        filename (str): The original filename, for the message.

    Returns:
        dict with message and the row count, or an error answer.
    """

    refusal = _refuse_write(report)
    if refusal is not None:
        return refusal

    file_bytes = await request.body()
    if len(file_bytes) == 0:
        return JSONResponse({"message": "Ingen fil mottatt"}, status_code=400)

    with WRITE_LOCK:
        try:
            row_count = install_upload(report, file_bytes)
        except ValueError as error:
            return JSONResponse({"message": str(error)}, status_code=400)
        except OSError as error:
            return JSONResponse({"message": f"Filen kunne ikke skrives: {error}"}, status_code=500)
        reload_error = _finish_write(report)
        if reload_error is not None:
            return JSONResponse({"message": reload_error}, status_code=500)

    title = REPORTS[report]["title"]
    return {"message": f"{title} er erstattet med {filename}: {row_count} rader", "rows": row_count}


@router.get("/knowledge/download")
def download(report: str):
    """
    The current file of a report, as a download.

    Args:
        report (str): "a", "b" or "c".

    Returns:
        FileResponse, or a 404 answer.
    """

    if report not in REPORTS:
        return JSONResponse({"message": "Ukjent rapport"}, status_code=404)
    path = report_path(report)
    if not path.exists():
        return JSONResponse({"message": "Filen finnes ikke"}, status_code=404)
    # octet-stream so the desktop window offers a save dialog instead of showing the text
    return FileResponse(str(path), media_type="application/octet-stream", filename=REPORTS[report]["file"])


if __name__ == "__main__":
    import tempfile

    def write_csv(path: Path, header: list, data_rows: list) -> None:
        """Writes a small report file the way the exports look: UTF-8 with BOM, CRLF."""
        with open(path, "w", encoding="utf-8-sig", newline="") as csv_file:
            writer = csv.writer(csv_file)
            writer.writerow(header)
            writer.writerows(data_rows)

    def active_run() -> bool:
        """Stands in for server.py while a run is in progress."""
        return True

    def no_run() -> bool:
        """Stands in for server.py between runs."""
        return False

    with tempfile.TemporaryDirectory() as temp_dir:
        REPORTS_DIR = Path(temp_dir)
        write_csv(REPORTS_DIR / REPORT_A.name, HEADER_A, [
            ["Person En", "10000001", "Enhet en"],
            ["Person To", "10000002", "Enhet to"],
            ["", "10000003", "Uten navn"],
        ])
        write_csv(REPORTS_DIR / REPORT_B.name, HEADER_B, [
            ["600000001", "10000001", "Enhet en"],
            ["600000002", "Ukjent", "Ukjent"],
        ])
        write_csv(REPORTS_DIR / REPORT_C.name, HEADER_C, [
            ["10000001", "Enhet en"],
            ["10000002", "Enhet to"],
        ])
        assert reload_tables() is None

        # reading, searching and paging
        rows_a = read_report("a")
        assert len(rows_a) == 3
        assert read_report("a") is rows_a, "second read should come from the cache"
        assert len(filter_rows(rows_a, "person")) == 2
        assert len(filter_rows(rows_a, "TO enhet")) == 1
        assert len(filter_rows(rows_a, "")) == 3
        page_answer = rows("a", "", 9, 25)
        assert page_answer["page"] == 1 and page_answer["total"] == 3 and page_answer["pages"] == 1
        page_answer = rows("a", "", 2, 2)
        assert page_answer["size"] == DEFAULT_PAGE_SIZE, "an unknown size falls back"
        assert isinstance(rows("x", "", 1, 25), JSONResponse)

        # format rules
        assert validate_values("a", ["", "10000001", "x"]) is not None
        assert validate_values("a", ["Navn", "123", "x"]) is not None
        assert validate_values("a", ["Navn", "10000001", ""]) is None
        assert validate_values("b", ["123456789", "10000001", "x"]) is not None
        assert validate_values("b", ["600000009", "Ukjent", "x"]) is not None
        assert validate_values("b", ["600000009", "10000001", "x"]) is None
        assert validate_values("c", ["10000009", ""]) is not None
        assert validate_values("c", ["10000009", "Ny"]) is None
        assert validate_values("c", ["10000009"]) is not None

        # add: written with BOM and CRLF, backed up, reloaded
        answer = add_row({"report": "c", "values": ["10000009", " Ny enhet "]})
        assert isinstance(answer, dict), answer.body
        assert answer["index"] == 2
        assert read_report("c")[2] == ["10000009", "Ny enhet"]
        assert lookup_data.lookup_koststed("10000009")["status"] == "found"
        raw_file = (REPORTS_DIR / REPORT_C.name).read_bytes()
        assert raw_file.startswith(b"\xef\xbb\xbf") and b"\r\n" in raw_file
        assert (REPORTS_DIR / BACKUP_DIR_NAME / REPORT_C.name).exists()
        assert not (REPORTS_DIR / (REPORT_C.name + ".tmp")).exists()

        # conflicts and stale rows
        answer = add_row({"report": "c", "values": ["10000009", "Igjen"]})
        assert answer.status_code == 409
        answer = add_row({"report": "a", "values": ["Person En", "10000001", "Enhet en"]})
        assert answer.status_code == 409, "an identical row in A is refused"
        answer = add_row({"report": "a", "values": ["Person En", "10000002", "Enhet to"]})
        assert isinstance(answer, dict), "the same name with another koststed is allowed"
        answer = edit_row({"report": "c", "index": 2, "old_values": ["10000009", "Feil"], "values": ["10000009", "Endret"]})
        assert answer.status_code == 409
        answer = edit_row({"report": "c", "index": "2", "old_values": ["10000009", "Ny enhet"], "values": ["10000009", "Endret"]})
        assert answer.status_code == 409, "a non-integer index is stale"
        answer = edit_row({"report": "c", "index": 2, "old_values": ["10000009", "Ny enhet"], "values": ["10000001", "Endret"]})
        assert answer.status_code == 409, "an edit may not take another row's key"
        answer = edit_row({"report": "c", "index": 2, "old_values": ["10000009", "Ny enhet"], "values": ["10000009", "Endret"]})
        assert isinstance(answer, dict), answer.body
        assert lookup_data.lookup_koststed("10000009")["description"] == "Endret"

        # delete
        answer = delete_row({"report": "c", "index": 2, "old_values": ["10000009", "Endret"]})
        assert isinstance(answer, dict), answer.body
        assert len(read_report("c")) == 2
        assert lookup_data.lookup_koststed("10000009")["status"] == "not_in_catalog"

        # refused while a run is in progress
        run_is_active = active_run
        answer = add_row({"report": "c", "values": ["10000008", "Under kjøring"]})
        assert answer.status_code == 409
        run_is_active = no_run

        # upload: wrong columns and wrong encoding leave the file untouched
        try:
            install_upload("c", "Feil;kolonner\r\n1;2\r\n".encode("utf-8"))
            assert False, "other columns must be refused"
        except ValueError as error:
            assert "kolonnene" in str(error)
        try:
            install_upload("c", "Koststedsnr.,Koststedsnavn\r\n10000005,Blå enhet\r\n".encode("latin-1"))
            assert False, "latin-1 must be refused"
        except ValueError as error:
            assert "UTF-8" in str(error)
        assert len(read_report("c")) == 2
        assert not (REPORTS_DIR / (REPORT_C.name + ".tmp")).exists()
        good_file = "Koststedsnr.,Koststedsnavn\r\n10000005,Blå enhet\r\n".encode("utf-8-sig")
        assert install_upload("c", good_file) == 1
        assert _finish_write("c") is None
        assert lookup_data.lookup_koststed("10000005")["status"] == "found"
        assert lookup_data.lookup_koststed("10000001")["status"] == "not_in_catalog"

        # a file that loads as CSV but fails the loader after install is rolled back
        broken = "Koststedsnr.,Koststedsnavn\r\n".encode("utf-8-sig")
        try:
            install_upload("c", broken)
            assert False, "a file without rows must be refused"
        except ValueError:
            pass
        assert lookup_data.lookup_koststed("10000005")["status"] == "found"

        # the overview
        overview = reports()
        assert overview["locked"] is False
        assert overview["reports"][2]["rows"] == 1 and overview["reports"][2]["loaded"] == 1
        assert overview["reports"][0]["loaded"] == 3, "the row without a name is not loaded"
        assert overview["reports"][0]["rows"] == 4

    print("api_knowledge: all checks passed")





import sys
import json
import time
from datetime import datetime
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root / "src"))

from erp_list import COLUMN_KEYS, to_json
from mistral_ocr import SCREENSHOTS_DIR, OCR_MODEL, MISTRAL_SERVER, build_client, ocr_image
from ocr_parser import LOW_CONFIDENCE, parse_ocr_response


RESULTS_DIR = Path(__file__).resolve().parent / "results"
REPROCESS = False                # True sends every screenshot to Mistral again, saved response or not
PRICE_USD_PER_1000_PAGES = 2.0   # Mistral's announced OCR price per 1,000 pages; check the account's pricing page


def list_screenshots() -> list:
    """
    Every PNG in the screenshot folder, sorted by file name.

    Returns:
        list: Paths of the screenshots.
    """
    screenshots = []
    for path in sorted(SCREENSHOTS_DIR.iterdir()):
        if path.suffix.lower() == ".png":
            screenshots.append(path)
    return screenshots


def load_or_ocr(image_path: Path, client, result_dir: Path) -> tuple:
    """
    Reuse the saved OCR response for a screenshot, or call Mistral and save it.

    Args:
        image_path (Path): The screenshot.
        client: The Mistral client, or None when nothing may be sent (never happens in a run).
        result_dir (Path): evaluation/results/<stem>/.

    Returns:
        tuple: (raw_response dict, seconds the call took or 0.0, True when the saved response was reused).
    """
    response_path = result_dir / "ocr_response.json"
    if response_path.is_file() and not REPROCESS:
        raw_response = json.loads(response_path.read_text(encoding="utf-8"))
        return raw_response, 0.0, True

    started = time.perf_counter()
    raw_response = ocr_image(image_path, client)
    seconds = time.perf_counter() - started
    result_dir.mkdir(parents=True, exist_ok=True)
    response_path.write_text(json.dumps(raw_response, indent=2, ensure_ascii=False), encoding="utf-8")
    return raw_response, seconds, False


def pages_processed(raw_response: dict) -> int:
    """
    Pages Mistral billed for one response.

    Args:
        raw_response (dict): A saved OCR response.

    Returns:
        int: usage_info.pages_processed, or 0 when absent.
    """
    usage_info = raw_response.get("usage_info") or {}
    pages = usage_info.get("pages_processed")
    if pages is None:
        return 0
    return int(pages)


def describe_erp_list(erp_list: dict) -> dict:
    """
    Counts over one parsed structure, for the summary; no cell value is included.

    Args:
        erp_list (dict): The parsed structure.

    Returns:
        dict: rows, first_row, last_row, columns_filled, cells, cells_below_threshold, lowest_confidence.
    """
    rows = erp_list["rows"]
    filled_columns = set()
    cells = 0
    cells_below_threshold = 0
    lowest_confidence = None
    for row in rows:
        for key in COLUMN_KEYS:
            if row[key] is None:
                continue
            cells = cells + 1
            filled_columns.add(key)
            confidence = row["confidence"][key]
            if confidence is None:
                continue
            if confidence < LOW_CONFIDENCE:
                cells_below_threshold = cells_below_threshold + 1
            if lowest_confidence is None or confidence < lowest_confidence:
                lowest_confidence = confidence

    description = {
        "rows": len(rows),
        "first_row": rows[0]["row_number"] if rows else None,
        "last_row": rows[-1]["row_number"] if rows else None,
        "columns_filled": len(filled_columns),
        "cells": cells,
        "cells_below_threshold": cells_below_threshold,
        "lowest_confidence": lowest_confidence,
    }
    return description


def evaluate_screenshot(image_path: Path, client) -> dict:
    """
    OCR and parse one screenshot, saving both files under evaluation/results/<stem>/.

    Args:
        image_path (Path): The screenshot.
        client: The Mistral client.

    Returns:
        dict: One summary record: file, status, message, cached, pages, ocr_seconds, parse_seconds
              and the counts from describe_erp_list() when parsing succeeded.
    """
    result_dir = RESULTS_DIR / image_path.stem
    record = {
        "file": image_path.name,
        "status": "ok",
        "message": "",
        "cached": False,
        "pages": 0,
        "ocr_seconds": 0.0,
        "parse_seconds": 0.0,
    }

    try:
        raw_response, ocr_seconds, cached = load_or_ocr(image_path, client, result_dir)
    except Exception as error:
        record["status"] = "ocr_error"
        record["message"] = f"{type(error).__name__}: {error}"
        return record
    record["cached"] = cached
    record["ocr_seconds"] = ocr_seconds
    record["pages"] = pages_processed(raw_response)

    started = time.perf_counter()
    try:
        erp_list = parse_ocr_response(raw_response, image_path.name, None)
    except ValueError as error:
        record["status"] = "parse_error"
        record["message"] = str(error)
        record["parse_seconds"] = time.perf_counter() - started
        return record
    record["parse_seconds"] = time.perf_counter() - started

    (result_dir / "erp_list.json").write_text(to_json(erp_list), encoding="utf-8")
    record.update(describe_erp_list(erp_list))
    return record


def summarize_run(records: list, wall_seconds: float) -> dict:
    """
    Totals over the whole run: counts, pages, cost estimate and time.

    Args:
        records (list): One record per screenshot from evaluate_screenshot().
        wall_seconds (float): Wall time of the whole run.

    Returns:
        dict: The run summary.
    """
    totals = {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "model": OCR_MODEL,
        "server": MISTRAL_SERVER,
        "low_confidence_threshold": LOW_CONFIDENCE,
        "price_usd_per_1000_pages": PRICE_USD_PER_1000_PAGES,
        "screenshots": len(records),
        "ok": 0,
        "parse_errors": 0,
        "ocr_errors": 0,
        "sent_to_mistral": 0,
        "reused_saved_response": 0,
        "rows": 0,
        "cells": 0,
        "cells_below_threshold": 0,
        "lowest_confidence": None,
        "pages_sent_this_run": 0,
        "pages_in_all_results": 0,
        "cost_usd_this_run": 0.0,
        "cost_usd_all_results": 0.0,
        "ocr_seconds_this_run": 0.0,
        "parse_seconds": 0.0,
        "wall_seconds": wall_seconds,
    }
    for record in records:
        if record["status"] == "ok":
            totals["ok"] = totals["ok"] + 1
        elif record["status"] == "parse_error":
            totals["parse_errors"] = totals["parse_errors"] + 1
        else:
            totals["ocr_errors"] = totals["ocr_errors"] + 1

        if record["status"] != "ocr_error":
            if record["cached"]:
                totals["reused_saved_response"] = totals["reused_saved_response"] + 1
            else:
                totals["sent_to_mistral"] = totals["sent_to_mistral"] + 1
                totals["pages_sent_this_run"] = totals["pages_sent_this_run"] + record["pages"]
                totals["ocr_seconds_this_run"] = totals["ocr_seconds_this_run"] + record["ocr_seconds"]
            totals["pages_in_all_results"] = totals["pages_in_all_results"] + record["pages"]
        totals["parse_seconds"] = totals["parse_seconds"] + record["parse_seconds"]

        if record["status"] == "ok":
            totals["rows"] = totals["rows"] + record["rows"]
            totals["cells"] = totals["cells"] + record["cells"]
            totals["cells_below_threshold"] = totals["cells_below_threshold"] + record["cells_below_threshold"]
            lowest = record["lowest_confidence"]
            if lowest is not None:
                if totals["lowest_confidence"] is None or lowest < totals["lowest_confidence"]:
                    totals["lowest_confidence"] = lowest

    totals["cost_usd_this_run"] = totals["pages_sent_this_run"] * PRICE_USD_PER_1000_PAGES / 1000
    totals["cost_usd_all_results"] = totals["pages_in_all_results"] * PRICE_USD_PER_1000_PAGES / 1000
    return totals


def format_record(record: dict) -> str:
    """
    One summary line for a screenshot.

    Args:
        record (dict): A record from evaluate_screenshot().

    Returns:
        str: The line.
    """
    source = "reused" if record["cached"] else f"ocr {record['ocr_seconds']:.1f} s"
    if record["status"] != "ok":
        return f"{record['file']} | {record['status']} | {record['message']} | {source}"
    lowest = record["lowest_confidence"]
    lowest_text = "n/a" if lowest is None else f"{lowest:.3f}"
    line = (
        f"{record['file']} | ok | rows {record['rows']} ({record['first_row']} to {record['last_row']})"
        f" | columns filled {record['columns_filled']}/{len(COLUMN_KEYS)} | cells {record['cells']}"
        f" | below {LOW_CONFIDENCE}: {record['cells_below_threshold']} | lowest {lowest_text}"
        f" | pages {record['pages']} | {source} | parse {record['parse_seconds'] * 1000:.0f} ms"
    )
    return line


def format_totals(totals: dict) -> str:
    """
    The run summary block.

    Args:
        totals (dict): From summarize_run().

    Returns:
        str: Several lines.
    """
    lowest = totals["lowest_confidence"]
    lowest_text = "n/a" if lowest is None else f"{lowest:.3f}"
    lines = [
        f"run at {totals['run_at']}, model {totals['model']}, server {totals['server']}",
        f"screenshots {totals['screenshots']}: ok {totals['ok']}, parse errors {totals['parse_errors']}, ocr errors {totals['ocr_errors']}",
        f"sent to Mistral {totals['sent_to_mistral']}, reused saved responses {totals['reused_saved_response']}",
        f"rows {totals['rows']}, cells {totals['cells']}, cells below {totals['low_confidence_threshold']}: {totals['cells_below_threshold']}, lowest confidence {lowest_text}",
        f"pages sent this run {totals['pages_sent_this_run']}, estimated cost {totals['cost_usd_this_run']:.4f} USD"
        f" (at {totals['price_usd_per_1000_pages']} USD per 1,000 pages)",
        f"pages in all results {totals['pages_in_all_results']}, estimated cost {totals['cost_usd_all_results']:.4f} USD",
        f"time: OCR calls this run {totals['ocr_seconds_this_run']:.1f} s, parsing {totals['parse_seconds']:.2f} s, whole run {totals['wall_seconds']:.1f} s",
    ]
    return "\n".join(lines)


def write_summary(records: list, totals: dict) -> None:
    """
    Write summary.txt (readable, counts only) and summary.json (the same, as data).

    Args:
        records (list): One record per screenshot.
        totals (dict): From summarize_run().
    """
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    lines = []
    for record in records:
        lines.append(format_record(record))
    text = "\n".join(lines) + "\n\n" + format_totals(totals) + "\n"
    (RESULTS_DIR / "summary.txt").write_text(text, encoding="utf-8")
    summary = {"totals": totals, "screenshots": records}
    (RESULTS_DIR / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    screenshots = list_screenshots()
    assert screenshots, f"no PNG screenshots in {SCREENSHOTS_DIR}"
    print(f"{len(screenshots)} screenshots, results in {RESULTS_DIR}")

    client = build_client()
    run_started = time.perf_counter()
    records = []
    for index, image_path in enumerate(screenshots, start=1):
        record = evaluate_screenshot(image_path, client)
        records.append(record)
        print(f"[{index}/{len(screenshots)}] {format_record(record)}")

    totals = summarize_run(records, time.perf_counter() - run_started)
    write_summary(records, totals)
    print()
    print(format_totals(totals))
    print(f"summary: {RESULTS_DIR / 'summary.txt'}")

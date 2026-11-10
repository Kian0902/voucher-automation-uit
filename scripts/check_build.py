"""
Starts a built program folder and checks that it works: no model, no browser, no real data.

Used by the Windows workflow right after the build, and usable on any machine against the
folder PyInstaller made from app/Fakturaassistent-windows.spec:

    python scripts/check_build.py dist/Fakturaassistent

It puts three tiny made-up report files next to the program, starts it once in smoke mode and
once for real without a browser, asks for every page, starts it a second time to see that the
second start yields to the first, quits it through its own quit button's endpoint, and then
removes everything the check left behind.
"""

import csv
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from lookup_data import HEADER_A, HEADER_B, HEADER_C, REPORT_A, REPORT_B, REPORT_C


URL = "http://127.0.0.1:8765"
START_TIMEOUT_SECONDS = 90
PAGES = ["/", "/agents", "/batch", "/knowledge", "/settings", "/setup", "/static/shell.js", "/static/app.css"]


def write_report(path: Path, header: list, rows: list) -> None:
    """
    Writes one made-up report the way the exports look: UTF-8 with BOM, CRLF.

    Args:
        path (Path): The file to write.
        header (list[str]): The column names the loader expects.
        rows (list[list[str]]): The data rows.
    """

    with open(path, "w", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.writer(csv_file)
        writer.writerow(header)
        writer.writerows(rows)


def get_json(path: str) -> dict:
    """
    Asks the running program for a JSON answer.

    Args:
        path (str): The path, for example "/status".

    Returns:
        dict: The parsed answer.
    """

    with urllib.request.urlopen(URL + path, timeout=5) as response:
        return json.loads(response.read())


def wait_for_status() -> dict:
    """
    Waits until the program answers on its port.

    Returns:
        dict: The first /status answer.

    Raises:
        RuntimeError: When nothing answered within START_TIMEOUT_SECONDS.
    """

    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            return get_json("/status")
        except (OSError, ValueError):
            time.sleep(0.5)
    raise RuntimeError(f"the program did not answer within {START_TIMEOUT_SECONDS} s")


def show_log(program_folder: Path) -> None:
    """
    Prints the program's own log, which is where a windowed program reports its errors.

    Args:
        program_folder (Path): The folder of the built program.
    """

    log_path = program_folder / "Fakturaassistent.log"
    if log_path.exists():
        print("--- Fakturaassistent.log ---")
        print(log_path.read_text(encoding="utf-8", errors="replace"))
        print("--- end of log ---")


if __name__ == "__main__":
    program_folder = Path(sys.argv[1]).resolve()
    executable = program_folder / "Fakturaassistent.exe"
    if not executable.exists():
        executable = program_folder / "Fakturaassistent"
    assert executable.exists(), f"no program in {program_folder}"

    reports_folder = program_folder / "data" / "koststed_tables"
    reports_folder.mkdir(parents=True, exist_ok=True)
    write_report(reports_folder / REPORT_A.name, HEADER_A, [["Person En", "10000001", "Enhet en"]])
    write_report(reports_folder / REPORT_B.name, HEADER_B, [["600000001", "10000001", "Enhet en"]])
    write_report(reports_folder / REPORT_C.name, HEADER_C, [["10000001", "Enhet en"]])

    process = None
    try:
        # 1. smoke mode: the program starts, loads the reports, writes its status and exits
        smoke_environment = dict(os.environ)
        smoke_environment["FAKTURAASSISTENT_SMOKE"] = "1"
        smoke_run = subprocess.run([str(executable)], env=smoke_environment, timeout=180)
        smoke_line = (program_folder / "smoke.txt").read_text(encoding="utf-8").strip()
        print(smoke_line)
        assert smoke_run.returncode == 0, f"smoke mode ended with {smoke_run.returncode}"
        assert "data_error=None" in smoke_line, smoke_line
        assert (program_folder / "config.env").exists(), "the first start writes config.env"

        # 2. a real start, without a browser: every page answers and the quit button is offered
        run_environment = dict(os.environ)
        run_environment["FAKTURAASSISTENT_NO_BROWSER"] = "1"
        process = subprocess.Popen([str(executable)], env=run_environment)
        state = wait_for_status()
        assert state["status"] == "idle" and state["data_error"] is None, state
        assert state["can_quit"] is True, "browser mode offers the quit button"
        assert sorted(state["settings_missing"]) == ["API_KEY", "API_MODEL", "API_URL", "MISTRAL_API_KEY"]
        assert state["setup_needed"] is True, "without keys the setup page must come first"
        for page in PAGES:
            with urllib.request.urlopen(URL + page, timeout=5) as response:
                assert response.status == 200, page
        reports = get_json("/knowledge/reports")["reports"]
        assert [report["rows"] for report in reports] == [1, 1, 1], reports
        assert get_json("/dashboard")["totals"]["runs"] == 0
        assert get_json("/batch/state")["counts"]["total"] == 0
        print("pages, knowledge base, dashboard and batch queue answer")

        # 3. a second start finds the first and ends at once
        second_run = subprocess.run([str(executable)], env=run_environment, timeout=120)
        assert second_run.returncode == 0, f"the second start ended with {second_run.returncode}"
        assert process.poll() is None, "the first instance keeps running"
        print("a second start yields to the running one")

        # 4. quitting through the endpoint behind "Avslutt appen" ends the process
        quit_request = urllib.request.Request(URL + "/quit", data=b"", method="POST")
        with urllib.request.urlopen(quit_request, timeout=5) as response:
            assert response.status == 200
        process.wait(timeout=30)
        assert process.returncode == 0, f"the program ended with {process.returncode}"
        process = None
        print("the program quits when asked")
    finally:
        if process is not None:
            process.kill()
        show_log(program_folder)
        # leave the folder as the build made it
        for name in ["smoke.txt", "config.env", "Fakturaassistent.log"]:
            leftover = program_folder / name
            if leftover.exists():
                leftover.unlink()
        for folder_name in ["data", "logs", "output"]:
            shutil.rmtree(program_folder / folder_name, ignore_errors=True)

    print("check_build: all checks passed")

"""
The batch queue: many invoices and screenshots dropped at once, processed one at a time.

Each file becomes a job. One background thread takes the jobs in the order they were added and
starts each through the same run the agent page uses, so a batch job and a single run behave
alike and write the same records. When a job ends, the final state of its run is kept with the
job, so the batch page can show any finished file again. A failed file does not stop the queue.

The queue lives in memory: closing the app forgets it. The files in output/, the logs and the
dashboard records stay, as for any run.
"""

import threading
import time
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse


POLL_SECONDS = 0.1
RESULT_LABELS = ["løst", "må bekreftes", "flere funnet", "uløst"]
KINDS = {".xml": "faktura", ".png": "liste"}
# the answer start_run gives while another run is in progress; the worker then waits and tries again
BUSY_STATUS_CODE = 409

JOBS = []                       # the jobs in the order they were added
JOBS_LOCK = threading.Lock()
_next_id = 1
_worker_thread = None
_wake = threading.Event()       # set when there may be work, so the worker does not spin

router = APIRouter()


# HOOKS, replaced by server.py

def start_run(file_bytes: bytes, filename: str):
    """
    Start a run on one file, as the agent page does.

    server.py replaces this function with its own. On its own the module cannot run anything.

    Returns:
        tuple[int | None, tuple[str, int] | None]: The run id, or the message and status code
        when the run could not start.
    """

    return None, ("Kjøring er ikke koblet til", 500)


def read_state():
    """
    The id and the state of the run on the page right now. Replaced by server.py.

    Returns:
        tuple[int, dict]: The run id and a copy of the state.
    """

    return 0, {"status": "idle"}


def stop_run() -> bool:
    """
    Stop the run in progress at once. Replaced by server.py.

    Returns:
        bool: True when a run was stopped.
    """

    return False


# JOBS

def kind_of(filename: str):
    """
    The kind of run a file gets, from its extension.

    Args:
        filename (str): The original filename.

    Returns:
        str | None: "faktura" for XML, "liste" for PNG, None for anything else.
    """

    suffix = Path(filename).suffix.lower()
    return KINDS.get(suffix)


def new_job(filename: str, kind: str, data: bytes) -> dict:
    """
    A job waiting in the queue.

    Args:
        filename (str): The original filename.
        kind (str): "faktura" or "liste".
        data (bytes): The file content, kept until the job has finished.

    Returns:
        dict: The job, status "waiting".
    """

    global _next_id
    job = {
        "id": _next_id,
        "filename": filename,
        "kind": kind,
        "status": "waiting",     # waiting | running | done | error | stopped
        "error": None,
        "added_at": datetime.now().isoformat(timespec="seconds"),
        "duration_seconds": None,
        "summary": None,         # counts per result label, set when the job ends
        "result": None,          # the final state of the run, for the detail view
        "run_id": None,
        "data": data,
    }
    _next_id = _next_id + 1
    return job


def summarize(kind: str, snapshot: dict) -> dict:
    """
    How one finished run came out, as counts per result label.

    A list run counts its finished rows, an invoice run its problems.

    Args:
        kind (str): "faktura" or "liste".
        snapshot (dict): The final state of the run.

    Returns:
        dict: One count per result label, "handled" (rows or problems with a result) and
              "total" (rows in the screenshot, or problems found).
    """

    summary = {}
    for label in RESULT_LABELS:
        summary[label] = 0

    if kind == "liste":
        entries = snapshot.get("rows", [])
        for row in entries:
            if row.get("status") == "done" and row.get("resultat") in summary:
                summary[row["resultat"]] = summary[row["resultat"]] + 1
    else:
        entries = snapshot.get("problems", [])
        for problem in entries:
            if problem.get("status") in summary:
                summary[problem["status"]] = summary[problem["status"]] + 1

    handled = 0
    for label in RESULT_LABELS:
        handled = handled + summary[label]
    summary["handled"] = handled
    summary["total"] = len(entries)
    return summary


def progress_text(kind: str, state: dict) -> str:
    """
    One short line on how far the running job has come.

    Args:
        kind (str): "faktura" or "liste".
        state (dict): The live state of the run.

    Returns:
        str: "rad 3 av 12" for a list with its rows known, else the title of the active stage.
    """

    rows = state.get("rows", [])
    if kind == "liste" and len(rows) > 0:
        current_row = min(state.get("rows_done", 0) + 1, len(rows))
        return f"rad {current_row} av {len(rows)}"
    for stage in state.get("stages", []):
        if stage["status"] == "active":
            return stage["title"]
    return "pågår"


def job_for_page(job: dict) -> dict:
    """
    A job as the queue table shows it: everything but the file content and the full result.

    Args:
        job (dict): The job.

    Returns:
        dict: id, filename, kind, status, error, duration_seconds, summary, progress, run_id
              (which run of the app the job was, so the page can match the live state to it)
              and can_retry.
    """

    return {
        "id": job["id"],
        "filename": job["filename"],
        "kind": job["kind"],
        "status": job["status"],
        "error": job["error"],
        "duration_seconds": job["duration_seconds"],
        "summary": job["summary"],
        "progress": None,
        "run_id": job["run_id"],
        "can_retry": job["status"] in ("error", "stopped") and job["data"] is not None,
    }


def find_job(job_id: int):
    """
    The job with an id; the caller holds JOBS_LOCK.

    Args:
        job_id (int): The id.

    Returns:
        dict | None: The job, or None.
    """

    for job in JOBS:
        if job["id"] == job_id:
            return job
    return None


def has_unfinished_jobs() -> bool:
    """
    Whether a file is being processed or still waits in the queue.

    Returns:
        bool: True when the queue has work left.
    """

    with JOBS_LOCK:
        for job in JOBS:
            if job["status"] in ("waiting", "running"):
                return True
    return False


# THE WORKER

def _next_waiting_job():
    """
    The first job still waiting, in queue order.

    Returns:
        dict | None: The job, or None when nothing waits.
    """

    with JOBS_LOCK:
        for job in JOBS:
            if job["status"] == "waiting":
                return job
    return None


def _finish_job(job: dict, snapshot) -> None:
    """
    Close a job with the final state of its run.

    Args:
        job (dict): The job that ran.
        snapshot (dict | None): The final state, None when another run replaced it first.
    """

    with JOBS_LOCK:
        if snapshot is None:
            job["status"] = "error"
            job["error"] = "Resultatet ble overskrevet av en annen kjøring"
            return
        job["status"] = snapshot["status"]
        job["error"] = snapshot.get("error")
        job["duration_seconds"] = snapshot.get("duration_seconds")
        job["summary"] = summarize(job["kind"], snapshot)
        job["result"] = snapshot
        # a finished file is not needed again; a failed or stopped one is kept for "prøv igjen"
        if job["status"] == "done":
            job["data"] = None


def _work() -> None:
    """
    The queue's loop: start the next waiting job, wait until its run has ended, keep the result.

    A single run started from the agent page is respected: the loop waits until it is over.
    A job whose run cannot start at all, for example an unsupported file, is marked failed and
    the loop goes on.
    """

    while True:
        job = _next_waiting_job()
        if job is None:
            _wake.wait(timeout=1.0)
            _wake.clear()
            continue

        run_id, problem = start_run(job["data"], job["filename"])
        if run_id is None:
            message, status_code = problem
            if status_code == BUSY_STATUS_CODE:
                time.sleep(POLL_SECONDS)
                continue
            with JOBS_LOCK:
                if job["status"] == "waiting":
                    job["status"] = "error"
                    job["error"] = message
            continue

        with JOBS_LOCK:
            job["status"] = "running"
            job["run_id"] = run_id

        snapshot = None
        while True:
            current_run_id, state = read_state()
            if current_run_id != run_id:
                break
            if state["status"] != "running":
                snapshot = state
                break
            time.sleep(POLL_SECONDS)
        _finish_job(job, snapshot)


def _ensure_worker() -> None:
    """Start the queue's thread on first use and tell it there is work."""

    global _worker_thread
    if _worker_thread is None or not _worker_thread.is_alive():
        _worker_thread = threading.Thread(target=_work, daemon=True)
        _worker_thread.start()
    _wake.set()


# ENDPOINTS

@router.post("/batch/add")
async def add(request: Request, filename: str = "fil"):
    """
    Put one file in the queue. The page sends the dropped files one by one, in order.

    Args:
        request (Request): The request whose body is the raw file.
        filename (str): The original filename, from the query string.

    Returns:
        dict with the job as the queue shows it, or a 400 answer.
    """

    kind = kind_of(filename)
    if kind is None:
        return JSONResponse(
            {"message": f"{filename}: filtypen støttes ikke, bare XML og PNG"}, status_code=400,
        )
    data = await request.body()
    if len(data) == 0:
        return JSONResponse({"message": f"{filename}: filen er tom"}, status_code=400)

    with JOBS_LOCK:
        job = new_job(filename, kind, data)
        JOBS.append(job)
        answer = job_for_page(job)
    _ensure_worker()
    return {"job": answer}


@router.get("/batch/state")
def state() -> dict:
    """
    The queue for the page: every job, the counts per status and the totals per result.

    Returns:
        dict: jobs (list), counts (per status, plus total) and totals (per result label).
    """

    _, live_state = read_state()
    jobs = []
    counts = {"total": 0, "waiting": 0, "running": 0, "done": 0, "error": 0, "stopped": 0}
    totals = {}
    for label in RESULT_LABELS:
        totals[label] = 0

    with JOBS_LOCK:
        for job in JOBS:
            item = job_for_page(job)
            if job["status"] == "running":
                item["progress"] = progress_text(job["kind"], live_state)
            jobs.append(item)
            counts["total"] = counts["total"] + 1
            counts[job["status"]] = counts[job["status"]] + 1
            if job["summary"] is not None:
                for label in RESULT_LABELS:
                    totals[label] = totals[label] + job["summary"][label]
    return {"jobs": jobs, "counts": counts, "totals": totals}


@router.get("/batch/job")
def job_result(id: int):
    """
    The final state of one finished job, for the detail view.

    Args:
        id (int): The job's id.

    Returns:
        dict with job and result (None while the job waits or runs), or a 404 answer.
    """

    with JOBS_LOCK:
        job = find_job(id)
        if job is None:
            return JSONResponse({"message": "Filen er ikke i køen lenger"}, status_code=404)
        return {"job": job_for_page(job), "result": job["result"]}


@router.post("/batch/stop")
def stop_current():
    """
    Stop the job that is running; the queue goes on with the next file.

    Returns:
        A confirmation, or 409 when nothing runs.
    """

    with JOBS_LOCK:
        running = False
        for job in JOBS:
            if job["status"] == "running":
                running = True
    if not running or not stop_run():
        return JSONResponse({"message": "Ingen fil behandles akkurat nå"}, status_code=409)
    return {"message": "Filen er stoppet"}


@router.post("/batch/stop-all")
def stop_all():
    """
    Stop the running job and take every waiting file out of the line.

    The waiting files stay in the table as stopped, so the staff see what was not processed
    and can start any of them again.

    Returns:
        dict: How many waiting files were stopped.
    """

    stopped_count = 0
    with JOBS_LOCK:
        for job in JOBS:
            if job["status"] == "waiting":
                job["status"] = "stopped"
                job["error"] = "Ikke startet"
                stopped_count = stopped_count + 1
    stop_run()
    return {"message": "Køen er stoppet", "stopped": stopped_count}


@router.post("/batch/retry")
def retry(id: int):
    """
    Put a failed or stopped file back in line.

    Args:
        id (int): The job's id.

    Returns:
        A confirmation, or an error answer.
    """

    with JOBS_LOCK:
        job = find_job(id)
        if job is None:
            return JSONResponse({"message": "Filen er ikke i køen lenger"}, status_code=404)
        if job["status"] not in ("error", "stopped") or job["data"] is None:
            return JSONResponse({"message": "Filen kan ikke startes på nytt"}, status_code=409)
        job["status"] = "waiting"
        job["error"] = None
        job["duration_seconds"] = None
        job["summary"] = None
        job["result"] = None
        job["run_id"] = None
    _ensure_worker()
    return {"message": "Filen står i kø igjen"}


@router.post("/batch/remove")
def remove(id: int):
    """
    Take one file out of the table; the one that is running has to be stopped first.

    Args:
        id (int): The job's id.

    Returns:
        A confirmation, or an error answer.
    """

    with JOBS_LOCK:
        job = find_job(id)
        if job is None:
            return JSONResponse({"message": "Filen er ikke i køen lenger"}, status_code=404)
        if job["status"] == "running":
            return JSONResponse({"message": "Stopp filen før du fjerner den"}, status_code=409)
        JOBS.remove(job)
    return {"message": "Fjernet"}


@router.post("/batch/clear")
def clear():
    """
    Take every finished, failed and stopped file out of the table; waiting files stay.

    Returns:
        dict: How many files were removed.
    """

    with JOBS_LOCK:
        remaining = []
        for job in JOBS:
            if job["status"] in ("waiting", "running"):
                remaining.append(job)
        removed_count = len(JOBS) - len(remaining)
        JOBS[:] = remaining
    return {"message": "Ryddet", "removed": removed_count}


if __name__ == "__main__":
    import copy

    # a stand-in for server.py: one run at a time, each ends by itself unless it is "slow"
    fake = {"run_id": 0, "state": {"status": "idle"}, "lock": threading.Lock()}

    def fake_finish(run_id: int, final_state: dict, delay: float) -> None:
        """Ends a fake run after a delay, unless it was stopped or replaced meanwhile."""
        time.sleep(delay)
        with fake["lock"]:
            if fake["run_id"] == run_id and fake["state"]["status"] == "running":
                fake["state"] = final_state

    def fake_start_run(file_bytes: bytes, filename: str):
        with fake["lock"]:
            if fake["state"]["status"] == "running":
                return None, ("opptatt", BUSY_STATUS_CODE)
            if file_bytes == b"broken":
                return None, ("Oppslagsdataene kunne ikke lastes", 503)
            fake["run_id"] = fake["run_id"] + 1
            run_id = fake["run_id"]
            rows = [{"status": "pending"}, {"status": "pending"}]
            fake["state"] = {"status": "running", "error": None, "duration_seconds": None,
                             "rows": rows, "rows_done": 0, "problems": [], "stages": []}
        if filename.endswith(".png"):
            final_state = {"status": "done", "error": None, "duration_seconds": 1.5, "rows_done": 2,
                           "rows": [{"status": "done", "resultat": "løst"}, {"status": "done", "resultat": "uløst"}],
                           "problems": [], "stages": []}
        else:
            final_state = {"status": "done", "error": None, "duration_seconds": 0.5, "rows": [], "rows_done": 0,
                           "problems": [{"status": "må bekreftes"}], "stages": []}
        delay = 0.05
        if file_bytes == b"slow":
            delay = 30
        threading.Thread(target=fake_finish, args=(run_id, final_state, delay), daemon=True).start()
        return run_id, None

    def fake_read_state():
        with fake["lock"]:
            return fake["run_id"], copy.deepcopy(fake["state"])

    def fake_stop_run() -> bool:
        with fake["lock"]:
            if fake["state"]["status"] != "running":
                return False
            fake["state"] = dict(fake["state"])
            fake["state"]["status"] = "stopped"
            fake["state"]["duration_seconds"] = 0.2
            return True

    def add_job(filename: str, data: bytes) -> dict:
        """What /batch/add does after reading the body."""
        with JOBS_LOCK:
            job = new_job(filename, kind_of(filename), data)
            JOBS.append(job)
        _ensure_worker()
        return job

    def wait_for(condition, seconds: float = 5.0) -> None:
        """Waits until the condition holds, failing loudly when it never does."""
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(0.02)
        raise AssertionError("condition not reached: " + str([(j["filename"], j["status"]) for j in JOBS]))

    start_run = fake_start_run
    read_state = fake_read_state
    stop_run = fake_stop_run

    assert kind_of("a.XML") == "faktura" and kind_of("b.png") == "liste" and kind_of("c.pdf") is None

    # three files run one after the other, in order, and a broken one does not stop the rest
    first = add_job("a.png", b"x")
    second = add_job("b.xml", b"x")
    third = add_job("c.png", b"broken")
    fourth = add_job("d.xml", b"x")
    wait_for(lambda: fourth["status"] == "done")
    assert first["status"] == "done" and second["status"] == "done"
    assert first["run_id"] < second["run_id"] < fourth["run_id"], "one at a time, in order"
    assert third["status"] == "error" and "Oppslagsdataene" in third["error"]
    assert first["summary"]["løst"] == 1 and first["summary"]["uløst"] == 1 and first["summary"]["total"] == 2
    assert second["summary"]["må bekreftes"] == 1 and second["summary"]["handled"] == 1
    assert first["data"] is None, "a finished file is released"
    assert third["data"] is not None, "a failed file is kept for a retry"

    overview = state()
    assert overview["counts"] == {"total": 4, "waiting": 0, "running": 0, "done": 3, "error": 1, "stopped": 0}
    assert overview["totals"]["løst"] == 1 and overview["totals"]["må bekreftes"] == 2
    assert overview["jobs"][2]["can_retry"] is True and overview["jobs"][0]["can_retry"] is False
    assert job_result(first["id"])["result"]["status"] == "done"
    assert job_result(999).status_code == 404

    # stopping the running file lets the next one start
    slow = add_job("slow.png", b"slow")
    after = add_job("after.xml", b"x")
    wait_for(lambda: slow["status"] == "running")
    overview = state()
    assert overview["jobs"][4]["progress"] == "rad 1 av 2", overview["jobs"][4]
    assert remove(slow["id"]).status_code == 409, "a running file cannot be removed"
    assert isinstance(stop_current(), dict)
    wait_for(lambda: after["status"] == "done")
    assert slow["status"] == "stopped" and slow["summary"]["handled"] == 0

    # stop all: the running file stops, the waiting ones are taken out of the line
    slow_two = add_job("slow2.png", b"slow")
    waiting_one = add_job("w1.xml", b"x")
    waiting_two = add_job("w2.xml", b"x")
    wait_for(lambda: slow_two["status"] == "running")
    answer = stop_all()
    assert answer["stopped"] == 2
    wait_for(lambda: slow_two["status"] == "stopped")
    assert waiting_one["status"] == "stopped" and waiting_one["error"] == "Ikke startet"
    assert stop_current().status_code == 409

    # retry puts a stopped file back, clear removes what is finished
    assert isinstance(retry(waiting_one["id"]), dict)
    wait_for(lambda: waiting_one["status"] == "done")
    assert retry(first["id"]).status_code == 409, "a finished file has no content left to run"
    assert isinstance(remove(waiting_two["id"]), dict)
    answer = clear()
    assert answer["removed"] == 8, answer
    assert JOBS == []

    print("api_batch: all checks passed")

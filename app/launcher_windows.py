"""
Starts the app on Windows: the server in a background thread, then the staff's own browser on it.

The Mac launcher (launcher_mac.py) opens a native window. On Windows the app runs in the default
browser instead: the native window there depends on WebView2 and .NET, and where those are
missing it falls back to the old Internet Explorer engine, which cannot run the pages. A browser
is always there and always current.

The program has no window of its own. It ends by itself a few seconds after the last browser
tab on it is closed, or at once through the "Avslutt appen" button in the sidebar. Starting it
again while it runs opens the browser on the instance that is already there, so there is never
more than one.

Prints and errors go to Fakturaassistent.log next to the program, since a windowed program on
Windows has no console to print to.
"""

import json
import os
import socket
import sys
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path


HOST = "127.0.0.1"
# fixed, so a second start finds the first one and a bookmark keeps working
PORT = 8765
# set to 1 to start the server, write its status to smoke.txt and exit: checks a build
SMOKE_VARIABLE = "FAKTURAASSISTENT_SMOKE"
# set to 1 to start for real but without opening a browser, for the build's own test
NO_BROWSER_VARIABLE = "FAKTURAASSISTENT_NO_BROWSER"
START_TIMEOUT_SECONDS = 60
# the pages ask /status twice a second; a longer silence means the last tab is closed
PAGE_GONE_SECONDS = 5
# the browser can be slow to come up; with no page at all after this long, the program ends
FIRST_PAGE_TIMEOUT_SECONDS = 120
WATCH_INTERVAL_SECONDS = 1
LOG_NAME = "Fakturaassistent.log"
SMOKE_RESULT_NAME = "smoke.txt"


def program_folder() -> Path:
    """
    The folder the program lives in.

    Returns:
        Path: The folder of the .exe when packaged, the repo root in development.
    """

    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def redirect_output() -> None:
    """
    Send prints and tracebacks to the log file when there is no console.

    A windowed program on Windows has no stdout and no stderr at all, both are None, and uvicorn
    asks stderr whether it is a terminal when it starts, which fails on None. The log file also
    gives the staff something to send when the program does not start.
    """

    if sys.stderr is not None and not getattr(sys, "frozen", False):
        return
    try:
        log_file = open(program_folder() / LOG_NAME, "a", encoding="utf-8", buffering=1)
    except OSError:
        log_file = open(os.devnull, "w", encoding="utf-8")
    sys.stdout = log_file
    sys.stderr = log_file


# before the server is imported: its imports already print and log
redirect_output()
sys.path.insert(0, str(Path(__file__).resolve().parent))

import uvicorn

import server
import api_batch


def answers_as_the_app(port: int) -> bool:
    """
    Whether this app already runs and answers on the port.

    Args:
        port (int): The port to ask.

    Returns:
        bool: True when /status answers with the app's own state.
    """

    try:
        with urllib.request.urlopen(f"http://{HOST}:{port}/status", timeout=2) as response:
            state = json.loads(response.read())
    except (OSError, ValueError):
        return False
    return isinstance(state, dict) and "stages" in state and "settings_missing" in state


def port_is_free(port: int) -> bool:
    """
    Whether nothing listens on the port.

    Args:
        port (int): The port to test.

    Returns:
        bool: True when the port could be bound.
    """

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind((HOST, port))
        except OSError:
            return False
    return True


def free_port() -> int:
    """
    Ask the system for a port nobody listens on right now.

    Returns:
        int: The port number.
    """

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind((HOST, 0))
        port = probe.getsockname()[1]
    return port


# The next two are the same as in launcher_mac.py, copied so that this launcher never imports the
# Mac one, which needs pywebview.
def serve(port: int) -> None:
    """
    Run the server until the process ends.

    Args:
        port (int): The localhost port to listen on.
    """

    uvicorn.run(server.app, host=HOST, port=port, log_level="warning")


def wait_until_listening(port: int) -> None:
    """
    Block until the server accepts connections.

    Args:
        port (int): The port the server was started on.

    Raises:
        RuntimeError: When the server has not answered within START_TIMEOUT_SECONDS.
    """

    deadline = time.monotonic() + START_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((HOST, port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.1)
    raise RuntimeError(f"the server did not start within {START_TIMEOUT_SECONDS} s")


def wait_for_quit_or_closed_tab() -> None:
    """
    Block until "Avslutt appen" was pressed or no browser tab shows the app any more.

    The server notes when a page last asked /status. Once a page has shown, PAGE_GONE_SECONDS
    of silence means the last tab is closed. A gap in this loop itself, from a laptop that was
    asleep, is not silence from the page, so the clock restarts after it.
    """

    started = time.monotonic()
    last_check = started
    while not server.QUIT_REQUESTED.wait(WATCH_INTERVAL_SECONDS):
        now = time.monotonic()
        machine_slept = now - last_check > 2 * WATCH_INTERVAL_SECONDS
        last_check = now
        if machine_slept:
            if server.LAST_PAGE_SEEN is not None:
                server.LAST_PAGE_SEEN = now
            continue
        if server.LAST_PAGE_SEEN is None:
            if now - started > FIRST_PAGE_TIMEOUT_SECONDS:
                print(f"no page showed within {FIRST_PAGE_TIMEOUT_SECONDS} s, ending")
                return
            continue
        if now - server.LAST_PAGE_SEEN > PAGE_GONE_SECONDS:
            print("the last browser tab is closed, ending")
            return
    # let the answer to /quit reach the page before the process ends
    time.sleep(0.5)


def run_smoke_check(port: int) -> int:
    """
    Ask the started server for its status and write the answer next to the program.

    Args:
        port (int): The port the server listens on.

    Returns:
        int: 0 when the reports loaded, 1 when they did not.
    """

    with urllib.request.urlopen(f"http://{HOST}:{port}/status", timeout=5) as response:
        state = json.loads(response.read())
    line = f"smoke: status={state['status']} data_error={state['data_error']}"
    print(line)
    result_path = program_folder() / SMOKE_RESULT_NAME
    result_path.write_text(line + "\n", encoding="utf-8")
    if state["data_error"] is None:
        return 0
    return 1


if __name__ == "__main__":
    smoke = os.environ.get(SMOKE_VARIABLE) == "1"
    open_browser = os.environ.get(NO_BROWSER_VARIABLE) != "1"

    # a second start shows the app that is already running instead of starting another
    if not smoke and answers_as_the_app(PORT):
        if open_browser:
            webbrowser.open(f"http://{HOST}:{PORT}/")
        sys.exit(0)

    port = PORT
    if smoke or not port_is_free(PORT):
        port = free_port()

    # the pages show "Avslutt appen" only when the app runs in a browser
    server.BROWSER_MODE = True
    server_thread = threading.Thread(target=serve, args=(port,), daemon=True)
    server_thread.start()
    wait_until_listening(port)

    if smoke:
        sys.exit(run_smoke_check(port))

    print(f"Fakturaassistent started on http://{HOST}:{port}/")
    if open_browser:
        webbrowser.open(f"http://{HOST}:{port}/")

    wait_for_quit_or_closed_tab()
    sys.exit(0)

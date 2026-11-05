"""
Starts the app on a Mac: the server in a background thread, then a native window on it.

This is what the desktop icon runs. Closing the window ends the process, and with it the server.
"""

import json
import os
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path

import uvicorn
import webview

sys.path.insert(0, str(Path(__file__).resolve().parent))

from server import app, APP_TITLE


HOST = "127.0.0.1"
# set to 1 to start the server, print its status and exit: checks a build without opening a window
SMOKE_VARIABLE = "FAKTURAASSISTENT_SMOKE"
START_TIMEOUT_SECONDS = 15
WINDOW_WIDTH = 1400
WINDOW_HEIGHT = 900


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


def serve(port: int) -> None:
    """
    Run the server until the process ends.

    Args:
        port (int): The localhost port to listen on.
    """

    uvicorn.run(app, host=HOST, port=port, log_level="warning")


def wait_until_listening(port: int) -> None:
    """
    Block until the server accepts connections, so the window never opens on an empty page.

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


if __name__ == "__main__":
    port = free_port()
    server_thread = threading.Thread(target=serve, args=(port,), daemon=True)
    server_thread.start()
    wait_until_listening(port)

    if os.environ.get(SMOKE_VARIABLE) == "1":
        with urllib.request.urlopen(f"http://{HOST}:{port}/status", timeout=5) as response:
            state = json.loads(response.read())
        print(f"smoke: status={state['status']} data_error={state['data_error']}")
        if state["data_error"] is None:
            sys.exit(0)
        sys.exit(1)

    # the knowledge base's download button: the window shows a save dialog for the CSV
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.create_window(APP_TITLE, f"http://{HOST}:{port}/", width=WINDOW_WIDTH, height=WINDOW_HEIGHT)
    webview.start()

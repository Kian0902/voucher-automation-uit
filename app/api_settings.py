"""
The settings page: the model server, the model, the keys and where the app keeps its files.

The values live in the config file the app loaded at start (config.env next to the app, or
.env.local in the repo). Saving rewrites that file, keeping every other line, and hands the new
values to server.py, which rebuilds the model clients, so they apply at once. Keys are never
sent to the page in full, only their last characters.
"""

import os
import platform
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values
from fastapi import APIRouter
from fastapi.responses import JSONResponse
from openai import OpenAI


# set by server.py: the config file it loaded and the folders it uses
CONFIG_FILE = Path(".env.local")
FOLDERS = []            # [(label, Path)]
MODELS_TIMEOUT_SECONDS = 10


# HOOKS, replaced by server.py

def run_is_active() -> bool:
    """
    Whether an agent run is in progress right now; a save is refused while one is.

    Returns:
        bool: False on its own.
    """

    return False


def apply_settings(values: dict) -> None:
    """
    Take the saved values into use: server.py puts them in the environment and rebuilds the
    model clients. On its own the module only writes the file.

    Args:
        values (dict): key -> value or None, every setting as the file now has it.
    """

    return None

# the settings in the order the page shows them; a secret is never sent in full
SETTINGS = [
    {"key": "API_URL", "secret": False},
    {"key": "API_MODEL", "secret": False},
    {"key": "API_KEY", "secret": True},
    {"key": "MISTRAL_API_KEY", "secret": True},
    {"key": "USER_NAME", "secret": False},    # the name shown at the bottom of the menu
]

# a line that sets a variable, with or without export, spaces and quotes
ASSIGNMENT_PATTERN = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=")

router = APIRouter()


def setting_keys() -> list:
    """
    The keys of every setting, in page order.

    Returns:
        list[str]: The environment variable names.
    """

    keys = []
    for setting in SETTINGS:
        keys.append(setting["key"])
    return keys


def current_environment() -> dict:
    """
    The settings as the running process has them.

    Returns:
        dict: key -> value, None for a key that is not set.
    """

    values = {}
    for key in setting_keys():
        values[key] = os.environ.get(key)
    return values


def mask(value) -> str | None:
    """
    A secret as the page may show it: dots plus the last four characters.

    Args:
        value (str | None): The secret.

    Returns:
        str | None: "••••" plus the last four characters, dots only for a short value, None
                    when the value is not set.
    """

    if value is None or value == "":
        return None
    if len(value) <= 8:
        return "••••"
    return "••••" + value[-4:]


# THE CONFIG FILE

def read_config(path: Path) -> dict:
    """
    The settings as they stand in the config file.

    Args:
        path (Path): The config file; a missing file gives no values.

    Returns:
        dict: key -> value for every setting, None when the file does not set it.
    """

    file_values = {}
    if path.is_file():
        file_values = dotenv_values(path)
    values = {}
    for key in setting_keys():
        value = file_values.get(key)
        if value == "":
            value = None
        values[key] = value
    return values


def write_config(path: Path, updates: dict) -> None:
    """
    Sets values in the config file, keeping every other line.

    A line that sets one of the keys is replaced; a key the file does not set is appended.
    The file is written whole to a temporary file and moved into place, so a failure leaves
    the old file as it was. A missing file is created.

    Args:
        path (Path): The config file.
        updates (dict): key -> new value.
    """

    lines = []
    if path.is_file():
        lines = path.read_text(encoding="utf-8").splitlines()

    remaining = dict(updates)
    new_lines = []
    for line in lines:
        match = ASSIGNMENT_PATTERN.match(line)
        if match is not None and match.group(1) in remaining:
            key = match.group(1)
            new_lines.append(f'{key}="{remaining.pop(key)}"')
        else:
            new_lines.append(line)
    for key, value in remaining.items():
        new_lines.append(f'{key}="{value}"')

    temp_path = path.with_name(path.name + ".tmp")
    temp_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
    os.replace(temp_path, path)


def validate_updates(updates: dict) -> str | None:
    """
    Checks the values about to be written.

    Args:
        updates (dict): key -> new value, every value a stripped string.

    Returns:
        str | None: The Norwegian error text, or None when every value is fine.
    """

    for key, value in updates.items():
        if '"' in value or "\n" in value or "\r" in value:
            return f"{key} kan ikke inneholde anførselstegn eller linjeskift"
    if "API_URL" in updates:
        url = updates["API_URL"]
        if not url.startswith("http://") and not url.startswith("https://"):
            return "Adressen må starte med http:// eller https://"
    if "API_MODEL" in updates and updates["API_MODEL"] == "":
        return "Modell kan ikke være tom"
    if "USER_NAME" in updates and updates["USER_NAME"] == "":
        return "Navn kan ikke være tomt"
    return None


# THE MODEL SERVER

def list_models(url: str, key: str) -> list:
    """
    Asks the model server which models it serves right now.

    Args:
        url (str): The server's base URL, as API_URL.
        key (str): The key, as API_KEY.

    Returns:
        list[str]: The model ids, as the server lists them.
    """

    client = OpenAI(base_url=url, api_key=key, timeout=MODELS_TIMEOUT_SECONDS, max_retries=0)
    ids = []
    for model in client.models.list().data:
        ids.append(model.id)
    return ids


# ENDPOINTS

def _settings_state() -> dict:
    """
    Everything the page shows: the values, the folders and the app.

    Returns:
        dict: config_file, config_exists, settings (list), folders, about.
    """

    file_values = read_config(CONFIG_FILE)
    settings = []
    for setting in SETTINGS:
        key = setting["key"]
        file_value = file_values[key]
        item = {
            "key": key,
            "secret": setting["secret"],
            "is_set": file_value is not None and file_value != "",
        }
        if setting["secret"]:
            item["value"] = None
            item["masked"] = mask(file_value)
        else:
            item["value"] = file_value
            item["masked"] = None
        settings.append(item)

    folders = []
    for label, path in FOLDERS:
        folders.append({"label": label, "path": str(path), "exists": path.exists()})

    about = {
        "python": platform.python_version(),
        "packaged": bool(getattr(sys, "frozen", False)),
        "started": STARTED_AT,
    }
    return {
        "config_file": str(CONFIG_FILE),
        "config_exists": CONFIG_FILE.is_file(),
        "settings": settings,
        "folders": folders,
        "about": about,
    }


STARTED_AT = datetime.now().isoformat(timespec="seconds")


@router.get("/settings/state")
def settings_state() -> dict:
    """The settings as they stand, for the page."""

    return _settings_state()


@router.post("/settings/save")
def save_settings(payload: dict):
    """
    Writes changed values to the config file.

    Args:
        payload (dict): {"values": {key: value}}; a blank secret means unchanged, a blank
                        address or model is refused.

    Returns:
        dict with message and the new state, or an error answer.
    """

    sent = payload.get("values")
    if not isinstance(sent, dict):
        return JSONResponse({"message": "Ingen verdier mottatt"}, status_code=400)
    if run_is_active():
        return JSONResponse(
            {"message": "En kjøring pågår. Vent til den er ferdig før du endrer innstillingene"},
            status_code=409,
        )

    updates = {}
    for setting in SETTINGS:
        key = setting["key"]
        if key not in sent:
            continue
        value = str(sent[key]).strip()
        if setting["secret"] and value == "":
            continue  # the page sends a blank secret when it is left as it is
        updates[key] = value

    problem = validate_updates(updates)
    if problem is not None:
        return JSONResponse({"message": problem}, status_code=400)
    if len(updates) == 0:
        return {"message": "Ingenting å lagre", "state": _settings_state()}

    try:
        write_config(CONFIG_FILE, updates)
    except OSError as error:
        return JSONResponse({"message": f"Filen kunne ikke skrives: {error}"}, status_code=500)

    apply_settings(read_config(CONFIG_FILE))
    message = f"Lagret i {CONFIG_FILE.name} og tatt i bruk"
    return {"message": message, "state": _settings_state()}


@router.post("/settings/models")
def models(payload: dict):
    """
    The models the server serves, for the address and key on the page.

    Args:
        payload (dict): {"url": address, "key": a new key or blank for the saved one}.

    Returns:
        dict with models (list of ids), or a 502 answer when the server did not answer.
    """

    file_values = read_config(CONFIG_FILE)
    url = str(payload.get("url", "")).strip()
    key = str(payload.get("key", "")).strip()
    if url == "":
        url = file_values["API_URL"] or ""
    if key == "":
        key = file_values["API_KEY"] or ""
    if url == "":
        return JSONResponse({"message": "Adressen til modellserveren mangler"}, status_code=400)

    try:
        ids = list_models(url, key)
    except Exception as error:  # the client raises many types; the page only needs the text
        return JSONResponse(
            {"message": f"Fikk ikke kontakt med modellserveren: {error}"}, status_code=502,
        )
    return {"models": ids, "url": url}


if __name__ == "__main__":
    import tempfile

    with tempfile.TemporaryDirectory() as temp_dir:
        CONFIG_FILE = Path(temp_dir) / "config.env"

        # a missing file reads as unset and is created on save
        assert read_config(CONFIG_FILE) == {"API_URL": None, "API_MODEL": None, "API_KEY": None, "MISTRAL_API_KEY": None, "USER_NAME": None}
        write_config(CONFIG_FILE, {"API_URL": "http://server.test/v1", "API_MODEL": "model-a"})
        values = read_config(CONFIG_FILE)
        assert values["API_URL"] == "http://server.test/v1" and values["API_MODEL"] == "model-a"
        assert not (Path(temp_dir) / "config.env.tmp").exists()

        # a file in the .env.example style keeps its comments and other lines
        CONFIG_FILE.write_text(
            "\n# Example API envs\n"
            'API_URL = "http://old.test"   # The server endpoint\n'
            'API_KEY = "sk-old-key-1234"\n'
            "export API_MODEL=old-model\n"
            'OTHER_THING = "stays"\n',
            encoding="utf-8",
        )
        write_config(CONFIG_FILE, {"API_MODEL": "new-model", "MISTRAL_API_KEY": "mstrl-new-key-9876"})
        text = CONFIG_FILE.read_text(encoding="utf-8")
        assert "# Example API envs" in text and 'OTHER_THING = "stays"' in text
        assert 'API_MODEL="new-model"' in text and "old-model" not in text
        assert text.count("API_MODEL") == 1 and text.count("MISTRAL_API_KEY") == 1
        values = read_config(CONFIG_FILE)
        assert values == {
            "API_URL": "http://old.test", "API_MODEL": "new-model",
            "API_KEY": "sk-old-key-1234", "MISTRAL_API_KEY": "mstrl-new-key-9876", "USER_NAME": None,
        }, values

        # masking and the rules
        assert mask(None) is None and mask("") is None
        assert mask("short") == "••••" and mask("sk-old-key-1234") == "••••1234"
        assert validate_updates({"API_URL": "ftp://x"}) is not None
        assert validate_updates({"API_URL": "https://x"}) is None
        assert validate_updates({"API_MODEL": ""}) is not None
        assert validate_updates({"API_KEY": 'a"b'}) is not None

        # the state: secrets masked, the folders checked
        FOLDERS = [("Mappe", Path(temp_dir)), ("Mangler", Path(temp_dir) / "nei")]
        state = _settings_state()
        by_key = {}
        for item in state["settings"]:
            by_key[item["key"]] = item
        assert by_key["API_KEY"]["value"] is None and by_key["API_KEY"]["masked"] == "••••1234"
        assert by_key["API_URL"]["value"] == "http://old.test" and by_key["API_URL"]["is_set"] is True
        assert state["folders"][0]["exists"] is True and state["folders"][1]["exists"] is False
        assert "••••" not in text and "sk-old-key-1234" in text, "the file keeps the full key"

        # saving through the endpoint: blank secrets are left alone, bad values refused, and the
        # saved values are handed on to be taken into use
        applied = []
        apply_settings = applied.append
        answer = save_settings({"values": {"API_URL": "http://new.test/v1", "API_KEY": "", "MISTRAL_API_KEY": ""}})
        assert isinstance(answer, dict), answer.body
        assert read_config(CONFIG_FILE)["API_KEY"] == "sk-old-key-1234"
        assert read_config(CONFIG_FILE)["API_URL"] == "http://new.test/v1"
        assert applied[-1]["API_URL"] == "http://new.test/v1" and applied[-1]["API_KEY"] == "sk-old-key-1234"
        assert save_settings({"values": {"API_URL": "nope"}}).status_code == 400
        run_is_active = lambda: True  # noqa: E731
        assert save_settings({"values": {"API_URL": "http://x.test"}}).status_code == 409
        run_is_active = lambda: False  # noqa: E731
        assert save_settings({"values": {"API_MODEL": "  "}}).status_code == 400
        assert save_settings({"values": {}})["message"] == "Ingenting å lagre"
        assert save_settings({}).status_code == 400
        answer = save_settings({"values": {"API_KEY": "sk-fresh-key-5555"}})
        assert isinstance(answer, dict) and read_config(CONFIG_FILE)["API_KEY"] == "sk-fresh-key-5555"
        # the name: trimmed, and never blank
        assert isinstance(save_settings({"values": {"USER_NAME": " Kari Nordmann "}}), dict)
        assert read_config(CONFIG_FILE)["USER_NAME"] == "Kari Nordmann"
        assert save_settings({"values": {"USER_NAME": "  "}}).status_code == 400
        assert read_config(CONFIG_FILE)["USER_NAME"] == "Kari Nordmann"

        # the model list, with the server call stood in for
        def fake_list_models(url, key):
            assert url == "http://new.test/v1" and key == "sk-fresh-key-5555", (url, key)
            return ["model-1", "model-2"]
        real_list_models = list_models
        list_models = fake_list_models
        answer = models({"url": "", "key": ""})
        assert answer["models"] == ["model-1", "model-2"], answer
        list_models = real_list_models
        CONFIG_FILE.write_text("", encoding="utf-8")
        assert models({"url": "", "key": ""}).status_code == 400

    print("api_settings: all checks passed")

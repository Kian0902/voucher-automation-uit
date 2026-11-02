"""
OCR one screenshot of the ERP's blocked-invoice list with Mistral OCR and save the raw response.
"""

import os
import json
import base64
from pathlib import Path
from dotenv import load_dotenv

from mistralai.client import Mistral


_repo_root = Path(__file__).resolve().parent.parent
load_dotenv(_repo_root / ".env.local")

OCR_MODEL = "mistral-ocr-4-1"
MISTRAL_SERVER = "eu"       # EU endpoint; the screenshots may only go to a European location
TIMEOUT_MS = 60_000         # REQUIRED: unbounded calls hung on the UiT server; keep every client bounded
SCREENSHOTS_DIR = _repo_root / "data" / "screenshots_invoice_lists"
OUTPUT_DIR = _repo_root / "data" / "ocr_output"


def build_client() -> Mistral:
    """
    Build the Mistral client from the key in .env.local.

    Returns:
        Mistral: A client pointed at the EU endpoint with a bounded timeout.

    Raises:
        KeyError: When MISTRAL_API_KEY is missing, loud on purpose.
    """
    api_key = os.environ["MISTRAL_API_KEY"]
    client = Mistral(api_key=api_key, server=MISTRAL_SERVER, timeout_ms=TIMEOUT_MS)
    return client


def encode_image(image_path: Path) -> str:
    """
    Read a PNG and return it as the base64 data URL Mistral accepts for images.

    Args:
        image_path (Path): The screenshot file.

    Returns:
        str: "data:image/png;base64,<base64 text>".
    """
    image_bytes = image_path.read_bytes()
    base64_text = base64.b64encode(image_bytes).decode("utf-8")
    data_url = "data:image/png;base64," + base64_text
    return data_url


def ocr_image(image_path: Path, client: Mistral) -> dict:
    """
    Run Mistral OCR on one screenshot and return the full response as a plain dict.

    Args:
        image_path (Path): The screenshot file.
        client (Mistral): The client from build_client().

    Returns:
        dict: The response as the SDK serializes it, JSON-safe, nothing removed.
    """
    data_url = encode_image(image_path)
    document = {"type": "image_url", "image_url": data_url}
    response = client.ocr.process(
        model=OCR_MODEL,
        document=document, # type: ignore
        table_format="html",
        include_blocks=True,
        confidence_scores_granularity="word",
    )
    raw_response = response.model_dump(mode="json")
    return raw_response


def save_response(raw_response: dict, image_path: Path) -> Path:
    """
    Write the raw response next to the other data, one JSON file per screenshot.

    Args:
        raw_response (dict): The dict from ocr_image().
        image_path (Path): The screenshot file; its stem names the output file.

    Returns:
        Path: The JSON file written.
    """
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = OUTPUT_DIR / (image_path.stem + ".json")
    json_text = json.dumps(raw_response, indent=2, ensure_ascii=False)
    output_path.write_text(json_text, encoding="utf-8")
    return output_path


def collect_confidences(node, found: list) -> None:
    """
    Walk any nested response structure and collect every numeric "confidence" value.

    The exact shape of the confidence scores is not assumed; this finds them wherever they sit.

    Args:
        node: A dict, list or scalar from the response.
        found (list): The list that collects the numbers.
    """
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "confidence" and isinstance(value, (int, float)):
                found.append(float(value))
            else:
                collect_confidences(value, found)
    elif isinstance(node, list):
        for item in node:
            collect_confidences(item, found)


def print_summary(raw_response: dict, image_path: Path, output_path: Path) -> None:
    """
    Print what came back without printing any cell value.

    Args:
        raw_response (dict): The dict from ocr_image().
        image_path (Path): The screenshot file.
        output_path (Path): Where the raw response was saved.
    """
    print(f"screenshot: {image_path.name}")
    print(f"saved to:   {output_path}")
    print(f"model:      {raw_response.get('model')}")
    usage_info = raw_response.get("usage_info")
    print(f"usage:      {usage_info}")

    pages = raw_response.get("pages", [])
    print(f"pages:      {len(pages)}")
    for page in pages:
        page_index = page.get("index")
        page_keys = sorted(page.keys())
        tables = page.get("tables") or []
        blocks = page.get("blocks") or []
        markdown_length = len(page.get("markdown") or "")
        print(f"  page {page_index}: keys {page_keys}")
        print(f"  page {page_index}: {len(tables)} tables, {len(blocks)} blocks, markdown {markdown_length} chars")

    confidences = []
    collect_confidences(raw_response, confidences)
    if confidences:
        lowest = min(confidences)
        mean = sum(confidences) / len(confidences)
        print(f"confidence: {len(confidences)} values, lowest {lowest:.3f}, mean {mean:.3f}")
    else:
        print("confidence: no numeric 'confidence' values found in the response")


if __name__ == "__main__":
    # Change this name to OCR another screenshot. One screenshot per run by decision.
    screenshot_name = "Skjermbilde 2026-10-08 081004.png"
    image_path = SCREENSHOTS_DIR / screenshot_name

    assert image_path.is_file(), f"not a file: {image_path}"
    assert image_path.suffix.lower() == ".png", f"expected a PNG screenshot, got {image_path.suffix}"

    client = build_client()
    raw_response = ocr_image(image_path, client)
    output_path = save_response(raw_response, image_path)

    # The saved file must hold exactly what came back.
    reloaded = json.loads(output_path.read_text(encoding="utf-8"))
    assert reloaded == raw_response, "saved JSON does not round-trip"

    print_summary(raw_response, image_path, output_path)

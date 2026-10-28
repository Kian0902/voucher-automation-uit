"""
One screenshot of the ERP's blocked-invoice list, from image to the predefined structure.
"""

from pathlib import Path

from mistral_ocr import SCREENSHOTS_DIR, OUTPUT_DIR, build_client, ocr_image, save_response
from ocr_parser import PARSED_OUTPUT_DIR, parse_ocr_response, print_tables, save_structure


def process_screenshot(image_path: Path, category: str | None) -> dict:
    """
    OCR one screenshot, save the raw response, parse it, save and return the structure.

    Args:
        image_path (Path): The screenshot file.
        category (str | None): "yellow", "red", or None when not known.

    Returns:
        dict: The structure from erp_list, one row per row on the screenshot, with confidences.
    """
    client = build_client()
    raw_response = ocr_image(image_path, client)
    save_response(raw_response, image_path)
    erp_list = parse_ocr_response(raw_response, image_path.name, category)
    save_structure(erp_list, image_path.name)
    return erp_list


if __name__ == "__main__":
    # Change this name to process another screenshot. One screenshot per run by decision.
    screenshot_name = "Skjermbilde 2026-10-05 081724.png"
    category = "yellow"

    image_path = SCREENSHOTS_DIR / screenshot_name
    assert image_path.is_file(), f"not a file: {image_path}"

    erp_list = process_screenshot(image_path, category)
    print_tables(erp_list)
    print(f"raw response: {OUTPUT_DIR / (image_path.stem + '.json')}")
    print(f"structure:    {PARSED_OUTPUT_DIR / (image_path.stem + '.json')}")

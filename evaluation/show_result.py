"""
Print the tables of one saved evaluation result, for comparing.
"""

import sys
import json
from pathlib import Path

_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root / "src"))

from ocr_parser import print_tables


RESULTS_DIR = Path(__file__).resolve().parent / "results"


if __name__ == "__main__":
    # Change this name to view another result.
    screenshot_name = "Skjermbilde 2026-10-05 081724.png"

    result_path = RESULTS_DIR / Path(screenshot_name).stem / "erp_list.json"
    assert result_path.is_file(), f"no result at {result_path}; run evaluation/run_all_screenshots.py first"
    erp_list = json.loads(result_path.read_text(encoding="utf-8"))
    print_tables(erp_list)

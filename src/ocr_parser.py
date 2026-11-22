"""
Turn one saved Mistral OCR response into the predefined ERP-list structure.
"""

import re
import json
from html.parser import HTMLParser
from pathlib import Path

from rich import box
from rich.console import Console
from rich.table import Table
from rich.text import Text

from erp_list import COLUMNS, COLUMN_KEYS, RESULT_LABELS, new_list, new_row, to_json


_repo_root = Path(__file__).resolve().parent.parent
OCR_OUTPUT_DIR = _repo_root / "data" / "ocr_output"
PARSED_OUTPUT_DIR = _repo_root / "data" / "parsed_output"

# Cells with a confidence below this are highlighted on screen. A first guess, not a measured threshold.
LOW_CONFIDENCE = 0.99

# The 14 columns on screen, in the ERP's order: row number, checkbox, Zoom, then the eleven named
# columns. None marks a column that is not carried into the structure.
SCREEN_COLUMNS = ["row_number", None, None] + COLUMN_KEYS

# The full list has 14 columns. A screenshot may end anywhere to the right of Lev.nr, so a row has
# 4 to 14 cells; the columns outside the screenshot stay empty in the structure.
FULL_LAYOUT = 14
MIN_CELLS = 4

# The checkbox in column 2 as OCR read it on every row so far. The Zoom icon in column 3 comes
# back as a varying glyph or an empty cell, and on some screenshots as no cell at all, which
# shifts every value after it one column to the left.
CHECKBOX_GLYPH = "☐"
CHECKBOX_INDEX = 1
ZOOM_INDEX = 2
FAKTURADATO_INDEX = 7

ROW_NUMBER_PATTERN = re.compile(r"[0-9]+")
DATE_PATTERN = re.compile(r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}")

# The column where a screenshot ends may be cut in the middle, and OCR reads the visible part as a
# cell. Its value is kept only when it is provably whole: a column with one of these shapes, and
# the text matches. Ordrenr shows 0 when the invoice has no order.
WHOLE_PATTERNS = {
    "lev_nr": re.compile(r"[0-9]{6}"),
    "fakturadato": re.compile(r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}"),
    "ordrenr": re.compile(r"0|[0-9]{9}"),
    "forfallsdato": re.compile(r"[0-9]{2}\.[0-9]{2}\.[0-9]{4}"),
}

# Columns with a fixed format, checked after mapping so that a shifted row cannot pass silently.
# An empty cell passes: red rows leave Lev.nr empty. Organisasjonsnr is digits only, not nine
# digits, because one 10-digit value occurs in the screenshots.
ANCHOR_PATTERNS = {
    "lev_nr": re.compile(r"[0-9]{6}"),
    "fakturadato": DATE_PATTERN,
    "organisasjonsnr": re.compile(r"[0-9]+"),
    "forfallsdato": DATE_PATTERN,
}

# Characters a header may contain. The ERP draws a sort icon in the header of the sorted column,
# which OCR renders as a glyph such as "↗"; everything outside this set is dropped before comparing.
HEADER_CHARACTER_PATTERN = re.compile(r"[^A-Za-z0-9æøåÆØÅ .()]")


class _TableHtmlParser(HTMLParser):
    """
    Collect the rows of one HTML table with the character span of every cell.

    Rows are lists of cells. A cell is a dict with "text" (unescaped, surrounding whitespace
    stripped, "" when empty), "is_header" (a th cell), "start" and "end": character offsets of
    the cell content in the HTML string, end exclusive. The spans tie Mistral's words to cells.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self._current_row = None
        self._current_cell = None
        self._line_starts = [0]

    def feed(self, html_text: str) -> None:
        """
        Parse the whole HTML string.

        Args:
            html_text (str): The table as Mistral returned it.
        """
        # HTMLParser reports (line, column); the word offsets are absolute, so remember line starts.
        self._line_starts = [0]
        for index, character in enumerate(html_text):
            if character == "\n":
                self._line_starts.append(index + 1)
        super().feed(html_text)
        super().close()

    def _absolute_position(self) -> int:
        """
        Translate the parser's (line, column) position into an offset in the HTML string.

        Returns:
            int: Character offset of the tag or text the parser is handling.
        """
        line_number, column = self.getpos()
        line_start = self._line_starts[line_number - 1]
        return line_start + column

    def handle_starttag(self, tag, attrs):
        if tag == "tr":
            self._current_row = []
        elif tag in ("td", "th"):
            tag_start = self._absolute_position()
            content_start = tag_start + len(self.get_starttag_text()) # type: ignore
            self._current_cell = {
                "text_parts": [],
                "is_header": tag == "th",
                "start": content_start,
                "end": content_start,
            }

    def handle_data(self, data):
        if self._current_cell is not None:
            self._current_cell["text_parts"].append(data)

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._current_cell is not None:
            raw_text = "".join(self._current_cell["text_parts"])
            cell = {
                "text": raw_text.strip(),
                "is_header": self._current_cell["is_header"],
                "start": self._current_cell["start"],
                "end": self._absolute_position(),
            }
            if self._current_row is None:
                self._current_row = []
            self._current_row.append(cell)
            self._current_cell = None
        elif tag == "tr" and self._current_row is not None:
            self.rows.append(self._current_row)
            self._current_row = None


def parse_table_html(html_text: str) -> list:
    """
    Split Mistral's HTML table into rows of cells with character spans.

    Args:
        html_text (str): The table as Mistral returned it.

    Returns:
        list: One list of cell dicts per table row, header rows included.
    """
    parser = _TableHtmlParser()
    parser.feed(html_text)
    return parser.rows


def is_header_row(cells: list) -> bool:
    """
    Tell whether every cell of a row is a th cell.

    Args:
        cells (list): The cells of one row.

    Returns:
        bool: True for a header row.
    """
    for cell in cells:
        if not cell["is_header"]:
            return False
    return True


def is_empty_row(cells: list) -> bool:
    """
    Tell whether a row has no text at all, like the ERP's filter row under the header.

    Args:
        cells (list): The cells of one row.

    Returns:
        bool: True when every cell is empty.
    """
    for cell in cells:
        if cell["text"] != "":
            return False
    return True


def normalize_header(text: str) -> str:
    """
    Reduce a header cell to the characters a real header contains, so a sort icon does not count.

    Args:
        text (str): The header cell as OCR read it, for example "↗ Fakturadato".

    Returns:
        str: The text without foreign glyphs and surrounding whitespace, for example "Fakturadato".
    """
    cleaned = HEADER_CHARACTER_PATTERN.sub("", text)
    return cleaned.strip()


def verify_header(cells: list) -> None:
    """
    Stop unless a header row reads as the ERP's headers in the ERP's order.

    The row-number column has no header text and the checkbox renders as a glyph that varies,
    so column 1 must be empty and column 2 is not checked. Sort icons are dropped first.

    Args:
        cells (list): The cells of the header row.

    Raises:
        ValueError: On a header that does not match, which would misplace every column after it.
    """
    if len(cells) > len(SCREEN_COLUMNS):
        raise ValueError(f"header has {len(cells)} cells, the ERP list has {len(SCREEN_COLUMNS)}")
    for index, cell in enumerate(cells):
        if index == 1:
            continue
        if index == 0:
            expected = ""
        elif index == 2:
            expected = "Zoom"
        else:
            expected = COLUMNS[index - 3][1]
        if normalize_header(cell["text"]) != expected:
            raise ValueError(f"header column {index + 1} reads {cell['text']!r}, expected {expected!r}")


def cell_confidence(cell: dict, words: list) -> float | None:
    """
    The lowest confidence among the words whose start lies inside the cell's span.

    Args:
        cell (dict): A cell with "start" and "end" offsets.
        words (list): Mistral's word_confidence_scores entries: text, confidence, start_index.

    Returns:
        float | None: The lowest confidence, or None when no word starts inside the cell.
    """
    lowest = None
    for word in words:
        start_index = word["start_index"]
        if cell["start"] <= start_index < cell["end"]:
            confidence = float(word["confidence"])
            if lowest is None or confidence < lowest:
                lowest = confidence
    return lowest


def restore_zoom_cell(cells: list) -> list:
    """
    Put back the Zoom cell when OCR left it out, which happens on whole screenshots.

    Without it every value from Lev.nr onward sits one column to the left and would be mapped
    wrongly with no error. The repair is made only when the evidence is exact: the row is one cell
    short of a known layout, the checkbox is in its place, and the invoice date, which every row
    carries, sits one column left of its place. Any other row is left for verify_row_layout().

    Args:
        cells (list): The cells of one body row.

    Returns:
        list: The cells with an empty cell inserted at the Zoom position, or the cells unchanged.
    """
    one_short = MIN_CELLS <= len(cells) + 1 <= FULL_LAYOUT
    if not one_short:
        return cells
    if len(cells) < FAKTURADATO_INDEX:
        # The date column is outside the screenshot, so the shift cannot be proven.
        return cells
    if cells[CHECKBOX_INDEX]["text"] != CHECKBOX_GLYPH:
        return cells
    date_shifted = DATE_PATTERN.fullmatch(cells[FAKTURADATO_INDEX - 1]["text"]) is not None
    if not date_shifted:
        return cells
    zoom_cell = {"text": "", "is_header": False, "start": 0, "end": 0}
    return cells[:ZOOM_INDEX] + [zoom_cell] + cells[ZOOM_INDEX:]


def blank_cut_cell(cells: list) -> list:
    """
    Empty the last cell of a row from a screenshot that ends inside the list, unless it is whole.

    The column where the screenshot ends may be cut in the middle, and a half value, such as the
    first letters of a name, must not be saved as if it were the value. The cell is kept only when
    its column has a shape in WHOLE_PATTERNS and the text matches; the count is kept either way so
    that the columns keep their positions. A whole list of 14 cells is returned unchanged.

    Args:
        cells (list): The cells of one body row, 1 to 14 of them.

    Returns:
        list: The cells, with the last one emptied when it may be a fragment.
    """
    if len(cells) >= FULL_LAYOUT:
        return cells
    last_index = len(cells) - 1
    key = SCREEN_COLUMNS[last_index]
    pattern = WHOLE_PATTERNS.get(key)
    if pattern is not None and pattern.fullmatch(cells[last_index]["text"]) is not None:
        return cells
    empty_cell = {"text": "", "is_header": False, "start": 0, "end": 0}
    return cells[:last_index] + [empty_cell]


def verify_row_layout(cells: list) -> None:
    """
    Stop unless a body row has 4 to 14 cells, the counts a screenshot of the list can show.

    Args:
        cells (list): The cells of one body row.

    Raises:
        ValueError: On any other count, which would misplace every column after the gap.
    """
    if not MIN_CELLS <= len(cells) <= FULL_LAYOUT:
        raise ValueError(
            f"row {cells[0]['text']} has {len(cells)} cells, the ERP list shows {MIN_CELLS} to {FULL_LAYOUT}")


def verify_anchor_columns(row: dict) -> None:
    """
    Stop when a fixed-format column holds a value of another shape, the sign of a shifted row.

    Args:
        row (dict): A filled row from body_row_to_structure().

    Raises:
        ValueError: Naming the row number, the column and the value.
    """
    for key, pattern in ANCHOR_PATTERNS.items():
        value = row[key]
        if value is None:
            continue
        if pattern.fullmatch(value) is None:
            raise ValueError(f"row {row['row_number']}: {key} reads {value!r}, expected {pattern.pattern}")


def body_row_to_structure(cells: list, words: list) -> dict:
    """
    Map the cells of one body row onto the structure, by their position on screen.

    A row missing its Zoom cell is repaired first, and the cell where the screenshot ends is kept
    only when it is whole. The cell count is then checked, and the fixed-format columns are checked
    after mapping, so a row whose values sit in the wrong columns is rejected instead of saved.

    Args:
        cells (list): The cells of the row, column 1 first.
        words (list): Mistral's word_confidence_scores entries for the table.

    Returns:
        dict: A row from erp_list.new_row() with values and confidences filled in.

    Raises:
        ValueError: When the cell count matches no layout, the row number is not digits, or a
            fixed-format column holds a value of another shape.
    """
    cells = restore_zoom_cell(cells)
    cells = blank_cut_cell(cells)
    verify_row_layout(cells)
    row_number_text = cells[0]["text"]
    if ROW_NUMBER_PATTERN.fullmatch(row_number_text) is None:
        raise ValueError(f"row number reads {row_number_text!r}, not digits")
    row = new_row(int(row_number_text))

    for index, cell in enumerate(cells):
        key = SCREEN_COLUMNS[index]
        if key is None or key == "row_number":
            continue
        if cell["text"] == "":
            continue
        row[key] = cell["text"]
        row["confidence"][key] = cell_confidence(cell, words)

    verify_anchor_columns(row)
    return row


def parse_ocr_response(raw_response: dict, source_file: str, category: str | None) -> dict:
    """
    Turn one saved Mistral OCR response into the ERP-list structure.

    Args:
        raw_response (dict): The response as src/mistral_ocr.py saved it.
        source_file (str): File name of the screenshot.
        category (str | None): "yellow", "red", or None when not known.

    Returns:
        dict: The structure from erp_list.new_list() with one row per body row of the table.

    Raises:
        ValueError: Unless the response holds exactly one page with exactly one HTML table.
    """
    pages = raw_response["pages"]
    if len(pages) != 1:
        raise ValueError(f"expected one page, got {len(pages)}")
    tables = pages[0].get("tables") or []
    if len(tables) != 1:
        raise ValueError(f"expected exactly one table, got {len(tables)}")
    table = tables[0]
    if table.get("format") != "html":
        raise ValueError(f"expected an html table, got {table.get('format')!r}")
    words = table.get("word_confidence_scores") or []

    rows = parse_table_html(table["content"])
    erp_list = new_list(source_file, category)
    for cells in rows:
        if len(cells) == 0 or is_empty_row(cells):
            continue
        if is_header_row(cells):
            header_cells = cells
            if len(cells) < FULL_LAYOUT:
                # The header where the screenshot ends may be a fragment; its body cells are blanked anyway.
                header_cells = cells[:-1]
            verify_header(header_cells)
            continue
        row = body_row_to_structure(cells, words)
        erp_list["rows"].append(row)
    return erp_list


def parse_saved_response(json_path: Path, category: str | None) -> dict:
    """
    Parse a response file written by src/mistral_ocr.py; its stem names the screenshot.

    Args:
        json_path (Path): data/ocr_output/<screenshot stem>.json.
        category (str | None): "yellow", "red", or None.

    Returns:
        dict: The ERP-list structure.
    """
    raw_response = json.loads(json_path.read_text(encoding="utf-8"))
    source_file = json_path.stem + ".png"
    return parse_ocr_response(raw_response, source_file, category)


def save_structure(erp_list: dict, screenshot_name: str) -> Path:
    """
    Write the parsed structure as JSON, one file per screenshot, next to the raw OCR output.

    Args:
        erp_list (dict): The structure from parse_ocr_response().
        screenshot_name (str): File name of the screenshot; its stem names the output file.

    Returns:
        Path: The JSON file written.
    """
    PARSED_OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output_path = PARSED_OUTPUT_DIR / (Path(screenshot_name).stem + ".json")
    output_path.write_text(to_json(erp_list), encoding="utf-8")
    return output_path


def _highlighted(text: str, confidence: float | None) -> Text:
    """
    Wrap a cell for rich, in bold yellow when its confidence is below LOW_CONFIDENCE.

    Args:
        text (str): The cell text.
        confidence (float | None): The cell's confidence, None for an empty cell.

    Returns:
        Text: The cell as rich renders it; plain Text avoids markup clashes with cell content.
    """
    if confidence is not None and confidence < LOW_CONFIDENCE:
        return Text(text, style="bold yellow")
    return Text(text)


def _widest(values: list) -> int:
    """
    Length of the longest string in a list, at least 1.

    Args:
        values (list): Cell strings of one column.

    Returns:
        int: The width the column needs so that no value is cut.
    """
    width = 1
    for value in values:
        if len(value) > width:
            width = len(value)
    return width


def _build_table(title: str, erp_list: dict, cell_text: dict, justify: str, columns: list = COLUMNS) -> Table:
    """
    Build one boxed table over the rows, with every column at least as wide as its widest cell.

    Headers may wrap onto several lines; values never do, and are never cut.

    Args:
        title (str): Printed above the table.
        erp_list (dict): The structure from parse_ocr_response(), or a resolved list.
        cell_text (dict): {key: [(text, confidence) per row]} for every column in columns.
        justify (str): "left" or "right" for the columns.
        columns (list): (key, header) pairs after the row number; the eleven ERP columns by default.

    Returns:
        Table: A rich table ready for Console.print().
    """
    row_numbers = []
    for row in erp_list["rows"]:
        row_numbers.append(str(row["row_number"]))

    table = Table(title=title, box=box.ROUNDED)
    table.add_column("Nr", justify="right", min_width=_widest(row_numbers), overflow="fold")
    for key, header in columns:
        texts = []
        for text, confidence in cell_text[key]:
            texts.append(text)
        table.add_column(header, justify=justify, min_width=_widest(texts), overflow="fold") # type: ignore

    for row_index, row_number in enumerate(row_numbers):
        cells = [Text(row_number)]
        for key, header in columns:
            text, confidence = cell_text[key][row_index]
            cells.append(_highlighted(text, confidence))
        table.add_row(*cells)
    return table


def _needed_width(table: Table) -> int:
    """
    The terminal width the table needs so that rich does not shrink any column.

    Args:
        table (Table): A table built by _build_table().

    Returns:
        int: Sum of the column minimum widths, plus one space of padding on each side of every
             column and one border line per column plus one.
    """
    width = 0
    for column in table.columns:
        width = width + column.min_width + 2 # type: ignore
    width = width + len(table.columns) + 1
    return width


def _value_cell_text(rows: list) -> dict:
    """
    The eleven ERP columns as table cells: the value with its confidence, "" for an empty cell.

    Args:
        rows (list): The rows of a parsed or resolved list.

    Returns:
        dict: {key: [(text, confidence) per row]} for the eleven columns.
    """
    cell_text = {}
    for key in COLUMN_KEYS:
        column = []
        for row in rows:
            value = row[key]
            if value is None:
                column.append(("", None))
            else:
                column.append((value, row["confidence"][key]))
        cell_text[key] = column
    return cell_text


def build_value_table(erp_list: dict) -> Table:
    """
    Boxed table of the values, ERP headers as column titles, doubtful cells highlighted.

    Args:
        erp_list (dict): The structure from parse_ocr_response().

    Returns:
        Table: A rich table ready for Console.print().
    """
    cell_text = _value_cell_text(erp_list["rows"])
    title = f"{erp_list['source_file']}   category: {erp_list['category']}   rows: {len(erp_list['rows'])}"
    return _build_table(title, erp_list, cell_text, "left")


def build_confidence_table(erp_list: dict) -> Table:
    """
    Boxed table of the per-cell confidences, same layout as the value table.

    Args:
        erp_list (dict): The structure from parse_ocr_response().

    Returns:
        Table: A rich table ready for Console.print().
    """
    cell_text = {}
    for key in COLUMN_KEYS:
        column = []
        for row in erp_list["rows"]:
            confidence = row["confidence"][key]
            if confidence is None:
                column.append(("", None))
            else:
                column.append((f"{confidence:.3f}", confidence))
        cell_text[key] = column
    title = f"confidence per cell, the lowest word confidence inside the cell; below {LOW_CONFIDENCE} highlighted"
    return _build_table(title, erp_list, cell_text, "right")


def _print_table(table: Table) -> None:
    """
    Print one table without cutting a value.

    When the terminal is narrower than the table needs, it is printed at its full width with a
    note, so lines wrap in the terminal but every value stays whole.

    Args:
        table (Table): A table built by _build_table().
    """
    console = Console()
    needed_width = _needed_width(table)
    if needed_width > console.width:
        print(f"(this table needs {needed_width} columns, the terminal has {console.width}: widen the terminal for a clean view)")
        wide_console = Console(width=needed_width)
        wide_console.print(table)
    else:
        console.print(table)


def print_tables(erp_list: dict) -> None:
    """
    Print the value table and the confidence table.

    Args:
        erp_list (dict): The structure from parse_ocr_response().
    """
    _print_table(build_value_table(erp_list))
    _print_table(build_confidence_table(erp_list))


# Stage 2 output: the ERP columns again, then what the agent found for each row.
RESOLVED_COLUMNS = COLUMNS + [("funnet_koststed", "Funnet koststed"), ("resultat", "Resultat")]


def build_resolved_table(resolved_list: dict) -> Table:
    """
    Boxed table of a resolved list: the values, the koststeder found and the result label per row.

    Args:
        resolved_list (dict): The structure from list_agent.resolve_list().

    Returns:
        Table: A rich table ready for Console.print().
    """
    cell_text = _value_cell_text(resolved_list["rows"])

    found_column = []
    result_column = []
    for row in resolved_list["rows"]:
        found_column.append((", ".join(row["funnet_koststed"]), None))
        if row["resultat"] is None:
            result_column.append(("", None))
        else:
            result_column.append((row["resultat"], None))
    cell_text["funnet_koststed"] = found_column
    cell_text["resultat"] = result_column

    counts = []
    for label in RESULT_LABELS:
        count = 0
        for row in resolved_list["rows"]:
            if row["resultat"] == label:
                count = count + 1
        counts.append(f"{label}: {count}")
    title = f"{resolved_list['source_file']}   rows: {len(resolved_list['rows'])}   " + ", ".join(counts)
    return _build_table(title, resolved_list, cell_text, "left", RESOLVED_COLUMNS)


def print_resolved_tables(resolved_list: dict) -> None:
    """
    Print the resolved table, then the agent's explanation for every row.

    Args:
        resolved_list (dict): The structure from list_agent.resolve_list().
    """
    _print_table(build_resolved_table(resolved_list))
    print("begrunnelse per rad:")
    for row in resolved_list["rows"]:
        explanation = row["begrunnelse"]
        if explanation is None:
            explanation = ""
        print(f"  {row['row_number']}:")
        for line in explanation.split("\n"):
            print(f"     {line}")


def _cells(texts: list, is_header: bool = False) -> list:
    """
    Build cells with empty spans for the self-check.

    Args:
        texts (list): The text of each cell.
        is_header (bool): Whether the cells are th cells.

    Returns:
        list: Cells as parse_table_html() returns them.
    """
    cells = []
    for text in texts:
        cells.append({"text": text, "is_header": is_header, "start": 0, "end": 0})
    return cells


if __name__ == "__main__":
    # Made-up rows: a whole list, lists that end at every column from Lev.nr on, whole or cut in the
    # middle, with and without the Zoom cell, a row cut too short, and a row whose OCR split one cell.
    full_texts = ["1", CHECKBOX_GLYPH, "", "123456", "Firma AS", "70001", "Avdeling X", "12.11.2026",
                  "600000001", "987654321", "26.11.2026", "1 000,00", "Åpen", ""]
    row = body_row_to_structure(_cells(full_texts), [])
    assert row["ordrenr"] == "600000001" and row["organisasjonsnr"] == "987654321", row
    for count in range(MIN_CELLS, FULL_LAYOUT):
        last_key = SCREEN_COLUMNS[count - 1]
        whole_last = last_key in WHOLE_PATTERNS
        row = body_row_to_structure(_cells(full_texts[:count]), [])
        assert (row[last_key] is not None) == whole_last, (count, row)
        for earlier_key in SCREEN_COLUMNS[3:count - 1]:
            assert row[earlier_key] is not None, (count, earlier_key)
        for later_key in SCREEN_COLUMNS[count:]:
            assert row[later_key] is None, (count, later_key)
        cut_texts = full_texts[:count - 1] + [full_texts[count - 1][:2]]
        row = body_row_to_structure(_cells(cut_texts), [])
        assert row[last_key] is None, (count, row)
    row = body_row_to_structure(_cells(full_texts[:9] + ["0"]), [])
    assert row["ordrenr"] == "600000001" and row["organisasjonsnr"] is None, row
    row = body_row_to_structure(_cells(full_texts[:8] + ["0"]), [])
    assert row["ordrenr"] == "0", row
    no_zoom_texts = full_texts[:ZOOM_INDEX] + full_texts[ZOOM_INDEX + 1:10]
    row = body_row_to_structure(_cells(no_zoom_texts), [])
    assert row["lev_nr"] == "123456" and row["ordrenr"] == "600000001" and row["organisasjonsnr"] is None, row
    for bad_texts in (full_texts[:3], full_texts[:6] + ["Y"] + full_texts[6:10]):
        try:
            body_row_to_structure(_cells(bad_texts), [])
            raise AssertionError(f"accepted {bad_texts}")
        except ValueError:
            pass
    print("layout self-check passed")

    # Change this name to parse another screenshot's OCR response. One screenshot per run by decision.
    screenshot_name = "Skjermbilde 2026-10-08 081004.png"
    category = "yellow"

    json_path = OCR_OUTPUT_DIR / (Path(screenshot_name).stem + ".json")
    assert json_path.is_file(), f"missing {json_path}; run src/mistral_ocr.py on that screenshot first"

    erp_list = parse_saved_response(json_path, category)
    output_path = save_structure(erp_list, screenshot_name)
    print_tables(erp_list)
    print(f"saved to: {output_path}")

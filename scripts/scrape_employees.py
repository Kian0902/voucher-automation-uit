


import argparse
import json
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path
from urllib.error import URLError

from bs4 import BeautifulSoup


# where the index is written unless --output says otherwise
DEFAULT_OUTPUT = Path(__file__).resolve().parent.parent / "data" / "employees.json"


# set this to False for a full refresh. Very Slow (every blank campus is looked up again)
REUSE_CAMPUS = True


# Employee list pages, one per UiT unit ["url": "Title"]
UNIT_PAGES = {
    "https://uit.no/enhet/ansattealfabetisk?p_dimension_id=88163":
        "Fakultet for biovitenskap, fiskeri og økonomi",
    "https://uit.no/enhet/ansattealfabetisk?p_dimension_id=88147":
        "Fakultet for humaniora, samfunnsvitenskap og lærerutdanning",
    "https://uit.no/enhet/ansattealfabetisk?p_dimension_id=210108":
        "Fakultet for ingeniørvitenskap og teknologi",
    "https://uit.no/enhet/ansattealfabetisk?p_dimension_id=88131":
        "Fakultet for naturvitenskap og teknologi",
    "https://uit.no/enhet/ansattealfabetisk?p_dimension_id=88108":
        "Det helsevitenskapelige fakultet",
    "https://uit.no/enhet/ansattealfabetisk?p_dimension_id=88177":
        "Det juridiske fakultet",
    "https://uit.no/enhet/ansattealfabetisk?p_dimension_id=88178":
        "Norges arktiske universitetsmuseum og akademi for kunstfag",
    "https://uit.no/enhet/ansattealfabetisk?p_dimension_id=88227":
        "Avdeling for bygg og eiendom",
    "https://uit.no/enhet/ansattealfabetisk?p_dimension_id=88219":
        "Avdeling for IT",
    "https://uit.no/enhet/ansattealfabetisk?p_dimension_id=88214":
        "Avdeling for organisasjon og økonomi",
    "https://uit.no/enhet/ansattealfabetisk?p_dimension_id=88200":
        "Avdeling for forskning, utdanning og formidling",
    "https://uit.no/enhet/ansattealfabetisk?p_dimension_id=210102":
        "Ledelse og administrasjon i Harstad",
    "https://uit.no/enhet/ansattealfabetisk?p_dimension_id=247539":
        "Ledelse og administrasjon i Alta",
    # TODO add Universitetsledelsen, Rektors stab og Universitetsstyret...
}


# library employee listy has its own page with a different HTML layout (see parse_library_list)
LIBRARY_PAGES = {"https://uit.no/ub/vaarebibliotek/sted/ansatte": "Universitetsbiblioteket"}

# uit.no serves a different page to clients without a browser user agent
USER_AGENT = "Mozilla/5.0 (compatible; UiT invoice automation; internal use)"

# Per employee marker in each raw HTML layout... Counting it catches parsers that silently drop rows
STANDARD_MARKER = 'class="h3mn"'
LIBRARY_MARKER = 'class="col-lg-12 ansatt"'



def fetch_page(url: str):
    """
    Function for downloading one employee list page.

    Args:
        url (str): The list page URL, e.g. BFE_URL.

    Returns: (str) The page HTML, decoded as UTF-8.
    """

    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        raw_bytes = response.read()

    return raw_bytes.decode("utf-8")


def _clean(raw_text: str):
    """
    Strip a text fragment for mapping empty fields to None value

    Args:
        raw_text (str | None): Text as found in the page.

    Returns:
        stripped_text (str | None)

    Example:
        " Stipendiat "  -> "Stipendiat"
        "   "           -> None
    """

    if raw_text is None:
        return None

    stripped_text = raw_text.strip()
    if stripped_text == "":
        return None

    return stripped_text


def _tag_text(tag):
    """
    Whitespace-cleaned text of a tag, or None when the tag is missing.

    Args:
        tag (Tag | None): The element to read, or None when not found.

    Returns:
        str | None: The cleaned text, or None.
    """

    if tag is None:
        return None

    return _clean(tag.get_text(" "))


def _name_cell_lines(name_cell):
    """
    Split the name column into its text lines.

    The column holds the linked name, then the sub-unit and the campus as
    bare text separated by <br> tags:

        <div class="h3mn"><a ...>Etternavn, Fornavn</a></div>
        Norges fiskerihøgskole<br />Campus Tromsø<br /><br />

    Args:
        name_cell (Tag): The employee's first col-md-3 div.

    Returns:
        list[str]: Non-empty lines below the name, in page order.
    """

    lines = [_clean(raw_line) for raw_line in name_cell.get_text("\n").split("\n")]
    non_empty = [line for line in lines if line is not None]

    # first line is the name itself, which the caller reads from the link
    return non_empty[1:]


def parse_employee_list(html, source_url):
    """
    Extract every employee entry from one list page.

    Args:
        html       (str): The page HTML from fetch_page().
        source_url (str): The URL the HTML came from, stored on each record.

    Returns:
        list[dict]: One dict per employee with keys name, unit, campus,
                    title, email, source_url. Fields the page leaves empty
                    are None.

    Example:
        {"name": "Etternavn, Fornavn",
         "unit": "Norges fiskerihøgskole",
         "campus": "Tromsø",
         "title": "Stipendiat",
         "email": "fornavn.etternavn@uit.no",
         "profile_url": ...
         "source_url": "https://uit.no/enhet/ansattealfabetisk?..."}
    """

    soup = BeautifulSoup(html, "html.parser")

    employees = []
    for name_div in soup.find_all("div", class_="h3mn"):
        name_link = name_div.find("a")
        if name_link is None:
            continue

        # Each employee has four "col-md-3" columns (name, title, email, phone)
        name_cell = name_div.find_parent("div", class_="col-md-3")
        if name_cell is None:
            continue
        other_cells = name_cell.find_next_siblings("div", class_="col-md-3", limit=3)


        # Sub-unit and campus sit below the name
        detail_lines = _name_cell_lines(name_cell)
        unit = None
        campus = None
        for line in detail_lines:
            if line.startswith("Campus"):
                # a bare "Campus" carries no name, so _clean makes it None
                campus = _clean(line.removeprefix("Campus"))
            elif unit is None:
                unit = line

        title = None
        if len(other_cells) >= 1:
            title = _tag_text(other_cells[0])

        email = None
        if len(other_cells) >= 2:
            email = _tag_text(other_cells[1].find("a", href=lambda h: h is not None and h.startswith("mailto:")))
        
        employees.append({
            "name": _clean(name_link.get_text()),
            "unit": unit,
            "campus": campus,
            "title": title,
            "email": email,
            "profile_url": _clean(name_link.get("href")), # type: ignore
            "source_url": source_url
        })

    return employees


def parse_library_list(html, source_url):
    """
    Extract every employee entry from the university library's own page.

    The library uses a different layout from the other units: one block per employee, with the
    fields marked by icons rather than by column order:

        <div class="col-lg-12 ansatt">
          <div class="col-lg-6 name"><h5><a ...>Etternavn, Fornavn</a></h5>
            <b>Spesialbibliotekar</b>
          <div><i class="fa fa-street-view"></i> Avdeling for bibliotektjenester</div>
          <div><i class="fa fa-envelope"></i><a href="mailto:fornavn.etternavn@uit.no">...</a></div>

    Campus is left None here: this page writes it together with a room number ("Campus Harstad 152"),
    while the employee's own card states it cleanly, so fill_missing_campus() reads it from there.

    Args:
        html       (str): The page HTML from fetch_page().
        source_url (str): The URL the HTML came from, stored on each record.

    Returns:
        list[dict]: One dict per employee, same keys as parse_employee_list().
    """

    soup = BeautifulSoup(html, "html.parser")

    employees = []
    for block in soup.find_all("div", class_="ansatt"):
        name_cell = block.find("div", class_="name")
        if name_cell is None:
            continue

        name_link = name_cell.find("a")
        if name_link is None:
            continue

        title = _tag_text(name_cell.find("b"))

        # Unit sits in the div marked with the street-view icon
        unit_icon = block.find("i", class_="fa-street-view")
        unit = _tag_text(unit_icon.parent) if unit_icon is not None else None

        email = _tag_text(block.find("a", href=lambda h: h is not None and h.startswith("mailto:")))

        employees.append({
            "name": _clean(name_link.get_text()),
            "unit": unit,
            "campus": None,
            "title": title,
            "email": email,
            "profile_url": _clean(name_link.get("href")), # type: ignore
            "source_url": source_url
        })

    return employees


def fetch_profile_campus(profile_url):
    """
    Read one employee's campus from their own UiT page.

    The list page leaves the campus blank for some employees, but their profile
    states it in the row marked with the fa-university icon:

        <span><i class="fa fa-university fa-fw"></i> Tromsø</span>

    Args:
        profile_url (str): The employee's profile URL.

    Returns:
        str | None: The campus name, e.g. "Tromsø", or None if the page does not state one.
    """

    html = fetch_page(profile_url)
    soup = BeautifulSoup(html, "html.parser")

    icon = soup.find("i", class_="fa-university")
    campus = _tag_text(icon.parent) if icon is not None else None

    # normalize the campus name for tromsø
    if campus == "Tromso":
        campus = "Tromsø"

    return campus


def reuse_known_campus(employees, index_path, enabled=REUSE_CAMPUS):
    """
    Fill in campuses already looked up on an earlier run.

    Args:
        employees (list[dict]): Records from scrape_units().
        index_path (str | Path): Path to the previous data/employees.json. A missing file simply means nothing to reuse.
        enabled (bool): Whether to reuse at all. False looks every blank campus up
                        again, picking up employees who moved campus. Defaults to REUSE_CAMPUS.

    Returns:
        int: How many records got a campus from the previous index.
    """

    if not enabled or not Path(index_path).exists():
        return 0

    with open(index_path, encoding="utf-8") as json_file:
        previous_index = json.load(json_file)

    known_campus = {e["email"]: e["campus"] for e in previous_index["employees"]
                    if e["email"] is not None and e["campus"] is not None}

    reused_count = 0
    for employee in employees:
        if employee["campus"] is None and employee["email"] in known_campus:
            employee["campus"] = known_campus[employee["email"]]
            reused_count = reused_count + 1

    return reused_count


def fill_missing_campus(employees, delay_seconds=0.5):
    """
    Look up the campus of every employee the list page left blank.

    Args:
        employees (list[dict]): Records from parse_employee_list().
        delay_seconds (float): Pause between profile requests. Defaults to 0.5.

    Returns:
        int: How many records got a campus filled in.
    """

    incomplete = [e for e in employees
                  if e["campus"] is None and e["profile_url"] is not None]

    filled_count = 0
    started_at = time.monotonic()

    for index, employee in enumerate(incomplete):
        campus = fetch_profile_campus(employee["profile_url"])
        if campus is not None:
            employee["campus"] = campus
            filled_count = filled_count + 1

        # for showing progress... how far along, and how long the rest will take
        done_count = index + 1
        elapsed_seconds = time.monotonic() - started_at
        seconds_per_lookup = elapsed_seconds / done_count
        remaining_seconds = seconds_per_lookup * (len(incomplete) - done_count)

        found_text = campus if campus is not None else "no campus on page"
        print(f"  [{done_count:>4}/{len(incomplete)}] {employee['name']:<35.35} "
              f"-> {found_text:<12.12} "
              f"elapsed {elapsed_seconds:5.0f}s, ~{remaining_seconds:.0f}s left",
              flush=True)

        # being a polite guest on uit.no XD XD XDd
        if done_count < len(incomplete):
            time.sleep(delay_seconds)

    return filled_count


def write_index(employees, units, output_path):
    """
    Write the employee index to disk as JSON.

    Args:
        employees (list[dict]): Records from parse_employee_list().
        units (dict): {source_url: unit name} for every page scraped.
        output_path (str | Path): Where to write, e.g. data/employees.json.

    Returns:
        Path: The path written to. The index records the date it was
              fetched, so stale data is visible.
    """

    index = {
        "fetched": date.today().isoformat(),
        "source": "uit.no employee list pages",
        "units": units,
        "employees": employees
    }

    output_path = Path(output_path)
    with open(output_path, "w", encoding="utf-8") as json_file:
        json.dump(index, json_file, indent=2, ensure_ascii=False)

    return output_path


def validate_page(html, employees, url, marker):
    """
    Check that a page parsed cleanly, and fail loudly when it did not.

    Args:
        html      (str): The page HTML the employees were parsed from.
        employees (list[dict]): What the parser returned.
        url       (str): The page URL, for the error message.
        marker    (str): The layout's per-employee marker, e.g. STANDARD_MARKER.

    Returns:
        list[str]: Warnings worth printing but not worth stopping for.

    Raises:
        ValueError: If the page yielded no employees, if the parser found
                    fewer than the page holds, or if a record is missing
                    the fields every employee must have.
    """

    expected_count = html.count(marker)

    if len(employees) == 0:
        raise ValueError(
            f"No employees parsed from {url} - the page holds {expected_count} "
            f"marker(s) '{marker}'. Its layout is probably not the one this "
            f"parser was written for; check it with check_page()."
        )

    if len(employees) != expected_count:
        raise ValueError(
            f"Parsed {len(employees)} employees from {url} but the page holds "
            f"{expected_count} - the parser is skipping entries."
        )

    missing_name = [e for e in employees if e["name"] is None]
    missing_unit = [e for e in employees if e["unit"] is None]
    if len(missing_name) > 0 or len(missing_unit) > 0:
        raise ValueError(
            f"{url}: {len(missing_name)} employees without a name and "
            f"{len(missing_unit)} without a unit - the layout has changed."
        )

    # reporting missing email on real pages...
    warnings = []
    missing_email = [e for e in employees if e["email"] is None]
    if len(missing_email) > 0:
        warnings.append(f"{len(missing_email)} employees without an email")

    missing_title = [e for e in employees if e["title"] is None]
    if len(missing_title) == len(employees):
        warnings.append("no employee has a title - is the title column still there?")

    return warnings


def check_page(url, parser=parse_employee_list, marker=STANDARD_MARKER):
    """
    Try one employee list page and report how it parses, writing nothing.

    Args:
        url    (str): The list page URL to try.
        parser (callable): The parser to try it with. Defaults to
                           parse_employee_list.
        marker (str): That layout's per-employee marker. Defaults to
                      STANDARD_MARKER.

    Returns:
        list[dict]: The parsed employees, so the caller can inspect them.

    Example:
        check_page(new_url)                                  # standard layout
        check_page(ub_url, parse_library_list, LIBRARY_MARKER)
    """

    html = fetch_page(url)
    employees = parser(html, url)

    print(f"\n===== {url} =====")
    print(f"  employees on page : {html.count(marker)}")
    print(f"  employees parsed  : {len(employees)}")

    for field in ("name", "unit", "campus", "title", "email"):
        present_count = sum(1 for e in employees if e[field] is not None)
        print(f"  with {field:<7}      : {present_count}")

    units = sorted({e["unit"] for e in employees if e["unit"] is not None})
    print(f"  sub-units         : {len(units)}")
    for unit in units:
        print(f"      {unit}")

    if len(employees) > 0:
        print("  first record      :")
        print("      " + json.dumps(employees[0], indent=6, ensure_ascii=False).replace("\n", "\n      "))

    warnings = validate_page(html, employees, url, marker)
    for warning in warnings:
        print(f"  WARNING: {warning}")
    print("  parses cleanly")

    return employees


def scrape_units(unit_pages, parser=parse_employee_list, marker=STANDARD_MARKER):
    """
    Fetch and parse a set of employee list pages.

    Args:
        unit_pages (dict): {list page URL: unit name}, e.g. UNIT_PAGES.
        parser (callable): The parser matching those pages' layout.
                           Defaults to parse_employee_list.
        marker (str): That layout's per-employee marker, used to check that
                      nothing was skipped. Defaults to STANDARD_MARKER.

    Returns:
        list[dict]: Every unit's employees, in page order.

    Raises:
        ValueError: If a page does not parse cleanly (see validate_page).
    """

    employees = []
    for url, unit_name in unit_pages.items():
        html = fetch_page(url)
        page_employees = parser(html, url)

        # stopping runs when page layout has changed
        warnings = validate_page(html, page_employees, url, marker)
        employees.extend(page_employees)

        print(f"{unit_name}: {len(page_employees)} employees", flush=True)
        for warning in warnings:
            print(f"  WARNING: {warning}", flush=True)

    return employees


def build_index(output_path=DEFAULT_OUTPUT, reuse=REUSE_CAMPUS):
    """
    Scrape every configured page, resolve campuses and write the index.

    Args:
        output_path (str | Path): Where to write the index. Defaults to DEFAULT_OUTPUT.
        reuse (bool): Reuse campuses resolved by an earlier run instead of looking them
        up again. Defaults to REUSE_CAMPUS.

    Returns:
        Path: The index file written.

    Raises:
        ValueError: If a page does not parse cleanly (see validate_page).
    """

    employees = scrape_units(UNIT_PAGES)
    employees.extend(scrape_units(LIBRARY_PAGES, parse_library_list, LIBRARY_MARKER))
    all_pages = {**UNIT_PAGES, **LIBRARY_PAGES}

    # the list pages leave some campuses blank; their own pages state them
    reused_count = reuse_known_campus(employees, output_path, enabled=reuse)
    missing_count = sum(1 for e in employees if e["campus"] is None)
    print(f"\n{reused_count} campuses reused from the previous index (reuse = {reuse})")
    print(f"{missing_count} of {len(employees)} employees across "
          f"{len(all_pages)} units still without campus, looking them up:")
    filled_count = fill_missing_campus(employees)

    write_index(employees, all_pages, output_path)

    with_email = sum(1 for e in employees if e["email"] is not None)
    with_title = sum(1 for e in employees if e["title"] is not None)
    with_campus = sum(1 for e in employees if e["campus"] is not None)
    print(f"\n{len(employees)} employees from {len(all_pages)} units: "
          f"{with_email} with email, {with_title} with title, {with_campus} "
          f"with campus ({filled_count} filled from profile pages)")
    print(f"Wrote {output_path}")

    return output_path


def check_all_pages():
    """
    Check every configured page against its parser, writing nothing.

    Raises:
        ValueError: If any page does not parse cleanly.
    """

    for url in UNIT_PAGES:
        check_page(url)

    for url in LIBRARY_PAGES:
        check_page(url, parse_library_list, LIBRARY_MARKER)


def main():
    """
    Command line entry point.

    Returns:
        int: 0 on success, 1 when a page could not be fetched or parsed.
    """

    parser = argparse.ArgumentParser(description="Build the local index of UiT employees from uit.no.")
    parser.add_argument("--check", metavar="URL", help="parse one page and report what it yields, write nothing")
    parser.add_argument("--check-all", action="store_true", help="parse every configured page, write nothing")
    parser.add_argument("--fresh", action="store_true", help="look every blank campus up again instead of reusing the index")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help=f"where to write the index (default: {DEFAULT_OUTPUT})")
    args = parser.parse_args()

    try:
        if args.check is not None:
            check_page(args.check)
        elif args.check_all:
            check_all_pages()
        else:
            build_index(args.output, reuse=not args.fresh)
    except (ValueError, URLError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

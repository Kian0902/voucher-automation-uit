




import csv
import os
import re
import sys
from pathlib import Path

import psycopg
from dotenv import load_dotenv



# pointing to src folder for importing normalize file
_repo_root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_repo_root / "src"))
load_dotenv(_repo_root / ".env.local")

from normalize import normalize_name



# same with th data csv lists...
REPORTS_DIR = _repo_root / "data" / "A_B_C_Reports"
REPORT_A = REPORTS_DIR / "Ansattreferanse_med_koststed.csv"
REPORT_B = REPORTS_DIR / "Innkjøpsordrenummer_med_koststed.csv"
REPORT_C = REPORTS_DIR / "Koststed_med_beskrivelse.csv"

# manualloy checked number of rows in each csv file
EXPECTED_ROWS_A = 29590
EXPECTED_ROWS_B = 35920
EXPECTED_ROWS_C = 293



def read_rows(csv_path) -> tuple:
    """
    Function for reading one report CSV into a header list and data rows.

    Args:
        csv_path (Path): Path to the report CSV.

    Returns:
        tuple[list[str], list[list[str]]]: (header names, data rows)
    """

    with open(csv_path, encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.reader(csv_file)
        all_rows = list(reader)

    header = all_rows[0]
    H = []
    for col_name in header:
        H.append(col_name.strip())

    data_rows = all_rows[1:]

    return H, data_rows


def _cell(row: list, index: int) -> str | None:
    """
    Gets one cell value from a row, stripped.

    Args:
        row (list[str]): A data row from read_rows()
        index (int): Column index

    Returns:
        str | None: The value, or None if the cell is blank
    """

    raw_value = row[index]
    stripped_value = raw_value.strip()
    if stripped_value == "":
        return None
        
    return stripped_value

def _is_koststed(value: str):
    """
    Function checks if a value looks like a valid koststed (8 digits)...

    Args:
        value (str): The value to test

    Returns:
        bool: True if value is 8 digits

    """

    return re.fullmatch(r"\d{8}", value) is not None




def prepare_persons(data_rows: list) -> tuple:
    """
    Builds the insert rows for the persons table from report A.

    Rows without a usable name, sucg as empty or nothing left after normalizing,
    can never be looked up anyway, so they are skipped...

    Args:
        data_rows (list[list[str]]): Data rows of report A.

    Returns:
        tuple[list[tuple], int]: (insert rows, how many rows were skipped)
    """


    insert_rows = []
    skipped_count = 0
    for row in data_rows:
        original_name = _cell(row, 0)
        koststed = _cell(row, 1)
        koststed_name = _cell(row, 2)

        if original_name is None:
            skipped_count = skipped_count + 1
            continue

        key = normalize_name(original_name)
        if key == "":
            skipped_count = skipped_count + 1
            continue

        # the words of the key used for the partial token search
        tokens = key.split(" ")
        insert_rows.append((original_name, key, tokens, koststed, koststed_name))

    return insert_rows, skipped_count




def prepare_ios(data_rows):
    """
    Builds the insert rows for the ios table from report B.

    Rows where the koststed is not 8 digits (the "Ukjent" ones) keep their IO but get
    koststed NULL and the koststed_unknown flag set instead.

    Args:
        data_rows (list[list[str]]): Data rows of report B.

    Returns:
        tuple[list[tuple], int]: (insert rows, how many IOs have unknown koststed)
    """


    insert_rows = []
    unknown_count = 0

    for row in data_rows:
        io = _cell(row, 0)
        koststed = _cell(row, 1)
        koststed_name = _cell(row, 2)


        # "Ukjent" or anything else that is not 8 digits counts as unknown
        koststed_is_unknown = koststed is None or not _is_koststed(koststed)
        if koststed_is_unknown:
            unknown_count = unknown_count + 1
            koststed = None
            koststed_name = None

        insert_rows.append((io, koststed, koststed_name, koststed_is_unknown))
    return insert_rows, unknown_count


def prepare_koststeder(data_rows):
    """
    Builds the insert rows for the koststeder table from report C.

    Rows where the koststed is not 8 digits (the "Ukjent" row) are not real
    koststeder, so they are skipped.

    Args:
        data_rows (list[list[str]]): Data rows of report C.

    Returns:
        tuple[list[tuple], list[str | None]]: (insert rows, the skipped values)
    """

    insert_rows = []
    skipped_values = []
    for row in data_rows:
        koststed = _cell(row, 0)
        description = _cell(row, 1)

        if koststed is None or not _is_koststed(koststed):
            skipped_values.append(koststed)
            continue

        insert_rows.append((koststed, description))

    # print(type(insert_rows[3:5]))
    return insert_rows, skipped_values



def load_tables(connection, person_rows: list, io_rows: list, koststed_rows: list):
    """
    Empties the three tables and inserts the prepared rows.

    Args:
        connection:      Open connection to the database
        person_rows:     Rows from prepare_persons()
        io_rows:         Rows from prepare_ios()
        koststed_rows:   Rows from prepare_koststeder()
    """

    with connection.cursor() as cursor:

        # empty the tables first so the script can be rerun
        cursor.execute("TRUNCATE persons, ios, koststeder RESTART IDENTITY")
        cursor.executemany(
            "INSERT INTO persons (name_original, name_key, name_tokens, koststed, koststed_name)"
            " VALUES (%s, %s, %s, %s, %s)", person_rows)
        cursor.executemany(
            "INSERT INTO ios (io, koststed, koststed_name, koststed_unknown)"
            " VALUES (%s, %s, %s, %s)", io_rows)
        cursor.executemany(
            "INSERT INTO koststeder (koststed, description)"
            " VALUES (%s, %s)", koststed_rows)

    # execute changes
    connection.commit()



if __name__ == "__main__":
    
    header_a, rows_a = read_rows(REPORT_A)
    header_b, rows_b = read_rows(REPORT_B)
    header_c, rows_c = read_rows(REPORT_C)

    # The column layout is assumed below, so verify it before loading
    assert header_a == ["Navn", "Koststedsnr.", "Koststedsnavn"], header_a
    assert header_b == ["Innkjøpsordrenr (IO hode)", "Koststedsnr. (IO hode)", "Koststedsnavn (IO hode)"], header_b
    assert header_c == ["Koststedsnr.", "Koststedsnavn"], header_c
    assert len(rows_a) == EXPECTED_ROWS_A, f"report A: {len(rows_a)} data rows"
    assert len(rows_b) == EXPECTED_ROWS_B, f"report B: {len(rows_b)} data rows"
    assert len(rows_c) == EXPECTED_ROWS_C, f"report C: {len(rows_c)} data rows"

    person_rows, skipped_names = prepare_persons(rows_a)
    io_rows, unknown_ios = prepare_ios(rows_b)
    koststed_rows, skipped_koststeder = prepare_koststeder(rows_c)

    database_url = os.environ["DATABASE_URL"]
    with psycopg.connect(database_url) as connection:
        load_tables(connection, person_rows, io_rows, koststed_rows)

        with connection.cursor() as cursor:
            # test 1: table counts match what was prepared
            expected_counts = {
                "persons": len(person_rows),
                "ios": len(io_rows),
                "koststeder": len(koststed_rows)}
            for table_name, expected_count in expected_counts.items():
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                stored_count = cursor.fetchone()[0]
                assert stored_count == expected_count, \
                    f"{table_name}: {stored_count} stored, {expected_count} prepared"

            # test 2: round-trip the first prepared row of each table...
            first_person = person_rows[0]
            cursor.execute(
                "SELECT name_original FROM persons WHERE name_key = %s",
                (first_person[1],))
            found_names = []
            for result_row in cursor.fetchall():
                found_names.append(result_row[0])
            assert first_person[0] in found_names, "persons round-trip failed"

            first_io = io_rows[0]
            cursor.execute(
                "SELECT koststed FROM ios WHERE io = %s",
                (first_io[0],))
            assert cursor.fetchone()[0] == first_io[1], "ios round-trip failed"

            first_koststed = koststed_rows[0]
            cursor.execute(
                "SELECT description FROM koststeder WHERE koststed = %s",
                (first_koststed[0],))
            assert cursor.fetchone()[0] == first_koststed[1], "koststeder round-trip failed"

    print(f"persons:    {len(person_rows)} rows ({skipped_names} unusable name rows skipped)")
    print(f"ios:        {len(io_rows)} rows ({unknown_ios} with unknown koststed)")
    print(f"koststeder: {len(koststed_rows)} rows (skipped values: {skipped_koststeder})")
    print("ingest self-check passed")





import os
import psycopg
from pathlib import Path
from dotenv import load_dotenv


# Loading env var for db setup
load_dotenv(Path(__file__).resolve().parent.parent / ".env.local")



# The schema for creating tables
TABLES = {
    "persons": """
        CREATE TABLE persons (
            id SERIAL PRIMARY KEY,
            name_original TEXT NOT NULL,
            name_key TEXT NOT NULL,
            koststed TEXT,
            koststed_name TEXT
        )
    """,
    "ios": """
        CREATE TABLE ios (
            io TEXT PRIMARY KEY,
            koststed TEXT,
            koststed_name TEXT,
            koststed_unknown BOOLEAN NOT NULL DEFAULT FALSE
        )
    """,
    "koststeder": """
        CREATE TABLE koststeder (
            koststed TEXT PRIMARY KEY,
            description TEXT
        )
    """
}

# making lookup indexes in addition to the main keys
INDEXES = ["CREATE INDEX idx_persons_name_key ON persons (name_key)"]




def create_schema(connection):
    """
    Function for executing the db schema. It drop and recreate every table
    and index of the retrieval schema.

    Args:
        connection (psycopg.Connection): Thr open connection to the database.
    """


    with connection.cursor() as cursor:

        # Anstattreferanse_med_koststed
        for table_name in TABLES:
            cursor.execute(f"DROP TABLE IF EXISTS {table_name}")

        # Innkjøpsordrenummer_med_koststed
        for table_name, table_ddl in TABLES.items():
            cursor.execute(table_ddl)

        # Koststed_med_beskrivelse
        for index_ddl in INDEXES:
            cursor.execute(index_ddl)

    connection.commit()



def existing_tables(connection) -> list[str]:
    """
    Func for listing the table names existing in the public schema.

    Args:
        connection (psycopg.Connection): The open connection to the database.
    Returns:
        list[str]: Table names, sorted
    """


    # Running quert for getting tabe names
    query = """SELECT table_name FROM information_schema.tables WHERE table_schema = 'public'"""

    with connection.cursor() as cursor:
        cursor.execute(query)
        rows = cursor.fetchall()

    # storing names...
    names = []
    for row in rows:
        names.append(row[0])

    names.sort()
    return names



def count_rows(connection, table_name: str) -> int:
    """
    Function for counting the rows of one table.

    Args:
        connection (psycopg.Connection): Open connection to the database.
        table_name (str): Name of the table to count.

    Returns:
        int: Number of rows.
    """

    with connection.cursor() as cursor:
        cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
        result = cursor.fetchone()
    return result[0]


if __name__ == "__main__":
    database_url = os.environ["DATABASE_URL"]

    with psycopg.connect(database_url) as connection:
        create_schema(connection)

        # self checking that all three tables exist and are empty
        tables = existing_tables(connection)
        for table_name in TABLES:
            assert table_name in tables, f"table {table_name} was not created"
            row_count = count_rows(connection, table_name)
            assert row_count == 0, f"table {table_name} is not empty: {row_count} rows"

    print(f"Schema created: {', '.join(sorted(TABLES))}... self-check passed")



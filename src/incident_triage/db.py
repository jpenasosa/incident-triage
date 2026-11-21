"""SQLite connection handling and schema setup.

Schema lives in `schema/*.sql`, applied in filename order when the database file is
first created. There is no migrations tool: to pick up schema changes, delete the
database file and re-ingest.
"""

import sqlite3
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_DIR = Path(__file__).parent / "schema"


@contextmanager
def connect(db_path: Path) -> Generator[sqlite3.Connection, None, None]:
    """Open a connection and always close it. Transactions are the caller's concern."""
    # check_same_thread=False: a request's connection may be created and used on
    # different threadpool threads, but never concurrently.
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db(db_path: Path) -> bool:
    """Create the database and apply the schema, unless the file already exists.

    Called at app startup. Returns True if a new database was initialised.
    """
    if db_path.exists():
        return False
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect(db_path) as conn, conn:  # second `conn`: commit, or roll back on error
        for sql_file in sorted(SCHEMA_DIR.glob("*.sql")):
            conn.executescript(sql_file.read_text())
    return True

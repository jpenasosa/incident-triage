import sqlite3

import pytest

from incident_triage.db import connect, init_db


def test_connect_closes_the_connection(tmp_path):
    with connect(tmp_path / "x.db") as conn:
        conn.execute("SELECT 1")

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")


def test_init_db_applies_every_schema_file_and_is_idempotent(tmp_path):
    db = tmp_path / "nested" / "app.db"

    init_db(db)
    init_db(db)  # second run must not fail

    with connect(db) as conn:
        names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert {"incidents", "incidents_fts"} <= names

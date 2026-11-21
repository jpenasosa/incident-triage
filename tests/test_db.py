import sqlite3

import pytest

from incident_triage.db import connect, init_db


def test_connect_closes_the_connection(tmp_path):
    with connect(tmp_path / "x.db") as conn:
        conn.execute("SELECT 1")

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")


def test_init_db_creates_schema_for_a_new_database(tmp_path):
    db = tmp_path / "nested" / "app.db"

    assert init_db(db) is True

    with connect(db) as conn:
        names = {r["name"] for r in conn.execute("SELECT name FROM sqlite_master")}
    assert {"incidents", "incidents_fts"} <= names


def test_init_db_skips_an_existing_database(tmp_path):
    db = tmp_path / "app.db"
    init_db(db)
    with connect(db) as conn, conn:
        conn.execute("INSERT INTO incidents VALUES (1, '', '', '', '', '', '', '', '[]', '')")

    assert init_db(db) is False
    with connect(db) as conn:
        assert conn.execute("SELECT count(*) FROM incidents").fetchone()[0] == 1

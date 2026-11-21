import pytest
from factories import make_incident

from incident_triage.db import connect, init_db
from incident_triage.models import IngestResult
from incident_triage.repository import SqliteIncidentRepository


@pytest.fixture
def conn(tmp_path):
    db = tmp_path / "repo.db"
    init_db(db)
    with connect(db) as conn:
        yield conn


def fts_matches(conn, term: str) -> list[str]:
    rows = conn.execute(
        "SELECT i.id FROM incidents_fts f JOIN incidents i ON i.rowid = f.rowid"
        " WHERE incidents_fts MATCH ?",
        (term,),
    ).fetchall()
    return [r["id"] for r in rows]


def test_upsert_counts_new_unchanged_and_changed(conn):
    repo = SqliteIncidentRepository(conn)
    a, b = make_incident(id=1), make_incident(id=2)

    assert repo.upsert_many([a, b]) == IngestResult(ingested=2)
    assert repo.upsert_many([a, b]) == IngestResult(skipped=2)
    changed = make_incident(id=2, title="Overtime calculated at the wrong rate")
    assert repo.upsert_many([a, changed]) == IngestResult(updated=1, skipped=1)


def test_update_reindexes_search_text_without_duplicates(conn):
    repo = SqliteIncidentRepository(conn)
    repo.upsert_many([make_incident(id=1, title="Payroll run delayed")])

    repo.upsert_many([make_incident(id=1, title="Overtime miscalculated")])

    assert fts_matches(conn, "overtime") == [1]
    assert fts_matches(conn, "delayed") == []
    assert conn.execute("SELECT count(*) FROM incidents_fts").fetchone()[0] == 1

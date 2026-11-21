"""Persistence for Incidents: the repository protocol and its SQLite implementation.

All queries live here (the schema is in `schema/*.sql`). `incidents` holds the records;
`incidents_fts` (FTS5) indexes the searchable text, keyed by rowid = Incident id.
"""

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from typing import Protocol

from incident_triage.models import Incident, IngestResult


class IncidentRepository(Protocol):
    def upsert_many(self, incidents: Sequence[Incident]) -> IngestResult: ...

    def get(self, incident_id: int) -> Incident | None: ...


def content_hash(incident: Incident) -> str:
    canonical = json.dumps(incident.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(canonical.encode()).hexdigest()


class SqliteIncidentRepository:
    def __init__(self, conn: sqlite3.Connection):
        self._conn = conn

    def upsert_many(self, incidents: Sequence[Incident]) -> IngestResult:
        """Insert new Incidents, update changed ones, skip unchanged ones. All or nothing."""
        result = IngestResult()
        with self._conn:  # one transaction: commit on success, rollback on error
            for incident in incidents:
                new_hash = content_hash(incident)
                row = self._conn.execute(
                    "SELECT content_hash FROM incidents WHERE id = ?", (incident.id,)
                ).fetchone()
                if row is None:
                    result.ingested += 1
                elif row["content_hash"] == new_hash:
                    result.skipped += 1
                    continue
                else:
                    result.updated += 1
                    self._conn.execute("DELETE FROM incidents_fts WHERE rowid = ?", (incident.id,))
                self._write(incident, new_hash)
        return result

    def get(self, incident_id: int) -> Incident | None:
        row = self._conn.execute(
            "SELECT id, created_at, environment, service, severity, title, description,"
            " resolution_summary, tags FROM incidents WHERE id = ?",
            (incident_id,),
        ).fetchone()
        return None if row is None else _to_incident(row)

    def _write(self, incident: Incident, new_hash: str) -> None:
        tags_json = json.dumps(incident.tags)
        self._conn.execute(
            """
            INSERT INTO incidents (id, created_at, environment, service, severity, title,
                                   description, resolution_summary, tags, content_hash)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                created_at = excluded.created_at, environment = excluded.environment,
                service = excluded.service, severity = excluded.severity,
                title = excluded.title, description = excluded.description,
                resolution_summary = excluded.resolution_summary, tags = excluded.tags,
                content_hash = excluded.content_hash
            """,
            (
                incident.id,
                incident.created_at.isoformat(),
                incident.environment,
                incident.service,
                incident.severity,
                incident.title,
                incident.description,
                incident.resolution_summary,
                tags_json,
                new_hash,
            ),
        )
        self._conn.execute(
            "INSERT INTO incidents_fts (rowid, title, description, resolution_summary, tags)"
            " VALUES (?, ?, ?, ?, ?)",
            (
                incident.id,
                incident.title,
                incident.description,
                incident.resolution_summary,
                " ".join(incident.tags),
            ),
        )


def _to_incident(row: sqlite3.Row) -> Incident:
    return Incident.model_validate({**dict(row), "tags": json.loads(row["tags"])})

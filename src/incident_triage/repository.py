"""Persistence for Incidents: the repository protocol and its SQLite implementation.

All queries live here (the schema is in `schema/*.sql`). `incidents` holds the records;
`incidents_fts` (FTS5) indexes the searchable text, keyed by rowid = Incident id.
"""

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from typing import Any, Protocol

from incident_triage.models import Incident, IngestResult, SearchPage, SearchQuery, SearchResult
from incident_triage.query import build_match_query

# bm25() column weights, in incidents_fts column order.
BM25_WEIGHTS = {"title": 3.0, "description": 1.0, "resolution_summary": 1.0, "tags": 2.0}
SNIPPET_TOKENS = 24
FILTER_ONLY_SNIPPET_CHARS = 160


class IncidentRepository(Protocol):
    def upsert_many(self, incidents: Sequence[Incident]) -> IngestResult: ...

    def get(self, incident_id: int) -> Incident | None: ...

    def search(self, query: SearchQuery) -> SearchPage: ...


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

    def search(self, query: SearchQuery) -> SearchPage:
        """Ranked full-text search when `q` has terms; otherwise filters only, newest first."""
        where, params = _filter_clause(query)
        page = {"limit": query.limit, "offset": query.offset}

        if query.q is None:
            total = self._count(f"SELECT count(*) FROM incidents i WHERE {where}", params)
            rows = self._conn.execute(
                f"""
                SELECT i.id, i.title, i.description AS snippet, NULL AS score,
                       i.service, i.environment, i.severity, i.created_at
                FROM incidents i WHERE {where}
                ORDER BY i.created_at DESC, i.id DESC LIMIT :limit OFFSET :offset
                """,
                {**params, **page},
            ).fetchall()
            results = [_to_result(r, truncate=True) for r in rows]
            return SearchPage(total=total, results=results, **page)

        match = build_match_query(query.q)
        if match is None:  # e.g. q="???": no searchable terms, so nothing can match
            return SearchPage(total=0, results=[], **page)

        weights = ", ".join(str(w) for w in BM25_WEIGHTS.values())
        fts_from = (
            "FROM incidents_fts JOIN incidents i ON i.id = incidents_fts.rowid"
            f" WHERE incidents_fts MATCH :match AND {where}"
        )
        params["match"] = match
        total = self._count(f"SELECT count(*) {fts_from}", params)
        rows = self._conn.execute(
            f"""
            SELECT i.id, i.title,
                   snippet(incidents_fts, 1, '', '', '…', {SNIPPET_TOKENS}) AS snippet,
                   -bm25(incidents_fts, {weights}) AS score,
                   i.service, i.environment, i.severity, i.created_at
            {fts_from}
            ORDER BY score DESC, i.created_at DESC, i.id DESC  -- ties: newest first
            LIMIT :limit OFFSET :offset
            """,
            {**params, **page},
        ).fetchall()
        return SearchPage(total=total, results=[_to_result(r) for r in rows], **page)

    def _count(self, sql: str, params: dict[str, Any]) -> int:
        return self._conn.execute(sql, params).fetchone()[0]

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


def _filter_clause(query: SearchQuery) -> tuple[str, dict[str, Any]]:
    """SQL conditions on `incidents i` for the query's filters (all must hold)."""
    conditions, params = ["1 = 1"], {}
    for column in ("environment", "service", "severity"):
        value = getattr(query, column)
        if value is not None:
            conditions.append(f"i.{column} = :{column}")
            params[column] = value.value
    for n, tag in enumerate(query.tags):
        conditions.append(f"EXISTS (SELECT 1 FROM json_each(i.tags) WHERE value = :tag{n})")
        params[f"tag{n}"] = tag
    return " AND ".join(conditions), params


def _to_result(row: sqlite3.Row, truncate: bool = False) -> SearchResult:
    data = dict(row)
    if truncate and len(data["snippet"]) > FILTER_ONLY_SNIPPET_CHARS:
        data["snippet"] = data["snippet"][: FILTER_ONLY_SNIPPET_CHARS - 1].rstrip() + "…"
    return SearchResult.model_validate(data)

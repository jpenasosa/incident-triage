import json
from typing import Any

from incident_triage.models import Incident, SearchResult


def incident_dict(**overrides: Any) -> dict[str, Any]:
    base = {
        "id": 1,
        "created_at": "2026-03-01T10:00:00Z",
        "environment": "prod",
        "service": "payroll",
        "severity": "SEV2",
        "title": "Payroll run delayed",
        "description": "The payroll run did not complete in time.",
        "resolution_summary": "Restarted the stalled batch job.",
        "tags": ["batch", "delay"],
    }
    return base | overrides


def make_incident(**overrides: Any) -> Incident:
    return Incident.model_validate(incident_dict(**overrides))


def ndjson(*rows: dict[str, Any]) -> str:
    return "\n".join(json.dumps(r) for r in rows) + "\n"


def make_result(id: int, **overrides: Any) -> SearchResult:
    data = {
        "id": id,
        "title": f"Incident {id}",
        "snippet": "…",
        "score": 1.0,
        "service": "payroll",
        "environment": "prod",
        "severity": "SEV2",
        "created_at": "2026-03-01T10:00:00Z",
    }
    return SearchResult.model_validate(data | overrides)

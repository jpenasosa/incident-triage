"""In-memory test doubles for the service layer."""

from collections.abc import Sequence

from incident_triage.models import Incident, IngestResult


class FakeIncidentRepository:
    """Records what the service hands to the repository."""

    def __init__(self, result: IngestResult | None = None):
        self.upserted: list[Incident] | None = None
        self._result = result or IngestResult()

    def upsert_many(self, incidents: Sequence[Incident]) -> IngestResult:
        self.upserted = list(incidents)
        return self._result

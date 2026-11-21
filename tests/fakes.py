"""In-memory test doubles for the service layer."""

from collections.abc import Sequence

from incident_triage.models import Incident, IngestResult


class FakeIncidentRepository:
    """Records what the service hands to the repository."""

    def __init__(self, result: IngestResult | None = None, incidents: Sequence[Incident] = ()):
        self.upserted: list[Incident] | None = None
        self._result = result or IngestResult()
        self._by_id = {i.id: i for i in incidents}

    def upsert_many(self, incidents: Sequence[Incident]) -> IngestResult:
        self.upserted = list(incidents)
        return self._result

    def get(self, incident_id: int) -> Incident | None:
        return self._by_id.get(incident_id)

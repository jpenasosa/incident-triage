"""In-memory test doubles for the service layer."""

from collections.abc import Sequence

from incident_triage.models import Incident, IngestResult, SearchPage, SearchQuery


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

    def search(self, query: SearchQuery) -> SearchPage:
        self.searched = query
        return SearchPage(total=0, limit=query.limit, offset=query.offset, results=[])


class FakeLLMClient:
    """Returns scripted replies (or raises scripted errors) in order; records each call."""

    def __init__(self, *replies):
        self._replies = list(replies)
        self.calls: list[dict] = []

    def complete(self, *, system, user, output):
        self.calls.append({"system": system, "user": user, "output": output})
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

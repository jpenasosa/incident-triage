"""In-memory test doubles for the service layer."""

from collections.abc import Sequence

from incident_triage.llm import AssistUnavailable
from incident_triage.models import Incident, IngestResult, SearchPage, SearchQuery, SearchResult


class FakeIncidentRepository:
    """Records what the service hands to the repository."""

    def __init__(
        self,
        result: IngestResult | None = None,
        incidents: Sequence[Incident] = (),
        search_results: Sequence[SearchResult] = (),
    ):
        self.upserted: list[Incident] | None = None
        self.searched: SearchQuery | None = None
        self._result = result or IngestResult()
        self._by_id = {i.id: i for i in incidents}
        self._search_results = list(search_results)

    def upsert_many(self, incidents: Sequence[Incident]) -> IngestResult:
        self.upserted = list(incidents)
        return self._result

    def get(self, incident_id: int) -> Incident | None:
        return self._by_id.get(incident_id)

    def search(self, query: SearchQuery) -> SearchPage:
        self.searched = query
        results = self._search_results[: query.limit]
        return SearchPage(
            total=len(self._search_results), limit=query.limit, offset=query.offset, results=results
        )


class FakeLLMClient:
    """Returns scripted replies (or raises scripted errors) in order; records each call."""

    def __init__(self, *replies, available: bool = True):
        self._replies = list(replies)
        self._available = available
        self.calls: list[dict] = []

    def ensure_available(self):
        if not self._available:
            raise AssistUnavailable("llm_key_missing", "LLM_API_KEY is not set")

    def complete(self, *, system, user, output):
        self.calls.append({"system": system, "user": user, "output": output})
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

"""In-memory test doubles for the service layer."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any

from incident_triage.llm import AssistUnavailable
from incident_triage.models import Incident, IngestResult, SearchPage, SearchQuery, SearchResult


class FakeIncidentRepository:
    """Records what the service hands to the repository."""

    def __init__(
        self,
        result: IngestResult | None = None,
        incidents: Sequence[Incident] = (),
        search_results: Sequence[SearchResult] | Callable[[SearchQuery], list[SearchResult]] = (),
    ):
        self.upserted: list[Incident] | None = None
        self.searched: SearchQuery | None = None
        self.searches: list[SearchQuery] = []
        self._result = result or IngestResult()
        self._by_id = {i.id: i for i in incidents}
        self._search_results = search_results

    def upsert_many(self, incidents: Sequence[Incident]) -> IngestResult:
        self.upserted = list(incidents)
        return self._result

    def get(self, incident_id: int) -> Incident | None:
        return self._by_id.get(incident_id)

    def search(self, query: SearchQuery) -> SearchPage:
        self.searched = query
        self.searches.append(query)
        hits = (
            self._search_results(query)
            if callable(self._search_results)
            else list(self._search_results)
        )
        return SearchPage(
            total=len(hits), limit=query.limit, offset=query.offset, results=hits[: query.limit]
        )


@dataclass
class ToolCall:
    """A scripted tool call for FakeLLMClient: the arguments the model 'sends'."""

    args: dict[str, Any]
    name: str = "search_incidents"


def search(q: str, **filters: Any) -> ToolCall:
    """A scripted search_incidents call; unset filters are null, as strict mode sends them."""
    blank = {"environment": None, "service": None, "severity": None, "tags": []}
    return ToolCall({"q": q, **blank, **filters})


@dataclass
class _Call:
    system: str
    user: str
    output: type
    max_tool_calls: int
    tool_outputs: list[str] = field(default_factory=list)

    def __getitem__(self, key: str):  # call["user"] etc., for terse assertions
        return getattr(self, key)


class FakeLLMClient:
    """Plays a scripted model: each ToolCall in `replies` runs the named tool, and the
    next other reply is the final answer (or an exception to raise). Records each call."""

    def __init__(self, *replies, available: bool = True):
        self._replies = list(replies)
        self._available = available
        self.calls: list[dict] = []

    def ensure_available(self):
        if not self._available:
            raise AssistUnavailable("llm_key_missing", "LLM_API_KEY is not set")

    def run_tools(self, *, system, user, tools, output, max_tool_calls):
        self.ensure_available()
        call = _Call(system, user, output, max_tool_calls)
        self.calls.append(call)
        by_name = {t.name: t for t in tools}
        while self._replies and isinstance(self._replies[0], ToolCall):
            scripted = self._replies.pop(0)
            tool = by_name[scripted.name]
            call.tool_outputs.append(tool.run(tool.args.model_validate(scripted.args)))
        reply = self._replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

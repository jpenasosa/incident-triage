"""Business logic for Incidents. The API layer delegates here; persistence is behind
IncidentRepository."""

import json
from typing import Any

import structlog
from pydantic import ValidationError

from incident_triage.assist import (
    ANALYSIS_SYSTEM_PROMPT,
    CUSTOMER_DRAFTS,
    INSUFFICIENT_NEXT_STEPS,
    MAX_TOOL_CALLS,
    SEARCH_TOOL_DESCRIPTION,
    SEARCH_TOOL_NAME,
    SearchIncidentsArgs,
    TriageAnalysis,
    apply_guardrail,
    build_analysis_input,
    build_next_steps,
    dedupe_by_title,
    merge_filters,
)
from incident_triage.errors import AppError, NotFoundError
from incident_triage.llm import LLMClient, Tool
from incident_triage.models import (
    AssistRequest,
    AssistResponse,
    CitedIncident,
    CustomerReply,
    Incident,
    IngestResult,
    SearchPage,
    SearchQuery,
)
from incident_triage.repository import IncidentRepository

MAX_INGEST_LINES = 1000
MAX_REPORTED_ERRORS = 50
CANDIDATE_LIMIT = 10

log = structlog.get_logger()


class InvalidIngestError(AppError):
    status_code = 422
    code = "INVALID_INCIDENTS"


class IncidentService:
    def __init__(self, repository: IncidentRepository, llm: LLMClient):
        self._repository = repository
        self._llm = llm

    def ingest_ndjson(self, text: str) -> IngestResult:
        """Validate every line first; write nothing unless the whole batch is valid."""
        incidents, errors = _parse_ndjson(text)
        if errors:
            log.info("ingest_rejected", problems=len(errors))
            raise InvalidIngestError(
                f"{len(errors)} problem(s) found; nothing was ingested",
                details=errors[:MAX_REPORTED_ERRORS],
            )
        result = self._repository.upsert_many(incidents)
        log.info("ingest_completed", **result.model_dump())
        return result

    def search(self, query: SearchQuery) -> SearchPage:
        if query.q is None and not query.has_filters:
            raise AppError("Provide a text query `q`, at least one filter, or both")
        page = self._repository.search(query)
        log.info(
            "search_completed",
            q=query.q,
            filters=query.model_dump(include={"environment", "service", "severity", "tags"}),
            total=page.total,
            returned=[r.id for r in page.results],
        )
        return page

    def assist(self, request: AssistRequest) -> AssistResponse:
        self._llm.ensure_available()  # fail fast: no search if assist can't finish
        shown: dict[int, Incident] = {}  # every record any search returned to the model
        search_tool = Tool(
            name=SEARCH_TOOL_NAME,
            description=SEARCH_TOOL_DESCRIPTION,
            args=SearchIncidentsArgs,
            run=lambda args: self._search_tool(args, request, shown),
        )
        log.info("assist_step", step="llm:analyze", phase="input", max_tool_calls=MAX_TOOL_CALLS)
        analysis = self._llm.run_tools(
            system=ANALYSIS_SYSTEM_PROMPT,
            user=build_analysis_input(request),
            tools=[search_tool],
            output=TriageAnalysis,
            max_tool_calls=MAX_TOOL_CALLS,
        )
        guarded = apply_guardrail(analysis, shown=shown)
        log.info(
            "assist_step",
            step="guardrail",
            shown_ids=sorted(shown),
            selected_ids=[c.id for c in guarded.cited],
            dropped=guarded.dropped,
            insufficient=guarded.insufficient,
        )
        if guarded.insufficient:
            if not shown:
                return self._insufficient("no_candidates")
            flagged = analysis.insufficient_context
            return self._insufficient("model_said_insufficient" if flagged else "none_verified")

        cited_records = [shown[c.id] for c in guarded.cited]
        reply = analysis.customer_reply
        log.info("assist_step", step="draft", customer_reply=reply, source="template")
        # Everything below comes from stored records, verified quotes or fixed templates.
        return AssistResponse(
            relevant_incidents=[
                CitedIncident(
                    id=c.id, title=shown[c.id].title, reason=f"Record states: “{c.evidence}”"
                )
                for c in guarded.cited
            ],
            next_steps=build_next_steps(cited_records),
            customer_draft=CUSTOMER_DRAFTS[reply],
            insufficient_context=False,
        )

    def _search_tool(
        self, args: SearchIncidentsArgs, request: AssistRequest, shown: dict[int, Incident]
    ) -> str:
        """search_incidents: the same search path as GET /search, with the engineer's
        filters enforced. Returns the full records of the deduplicated Candidates and adds
        them to `shown`. Invalid arguments go back to the model as an error, not a 4xx."""
        filters, overridden = merge_filters(args, request)
        log.info(
            "assist_step",
            step="tool:search",
            phase="input",
            q=args.q,
            filters=filters,
            overridden=overridden,
            limit=CANDIDATE_LIMIT,
        )
        try:
            query = SearchQuery(q=args.q, **filters, limit=CANDIDATE_LIMIT)
            page = self.search(query)
        except (ValidationError, AppError) as exc:
            message = exc.message if isinstance(exc, AppError) else "invalid search arguments"
            log.info("assist_step", step="tool:search", phase="output", error=message)
            return json.dumps({"error": message})

        candidates = dedupe_by_title(page.results)
        records = [r for r in (self._repository.get(c.id) for c in candidates) if r is not None]
        shown.update((r.id, r) for r in records)
        log.info(
            "assist_step",
            step="tool:search",
            phase="output",
            hits=page.total,
            top_ids=[c.id for c in page.results],
            collapsed_duplicates=len(page.results) - len(candidates),
        )
        log.debug(
            "assist_candidates",
            candidates=[
                {"id": c.id, "score": c.score, "service": c.service, "title": c.title}
                for c in page.results
            ],
        )
        return json.dumps(
            {"total": page.total, "incidents": [r.model_dump(mode="json") for r in records]}
        )

    def _insufficient(self, reason: str) -> AssistResponse:
        log.info("assist_insufficient", reason=reason)
        log.info(
            "assist_step",
            step="draft",
            customer_reply=CustomerReply.REQUEST_DETAILS,
            source="template",
        )
        return AssistResponse(
            relevant_incidents=[],
            next_steps=INSUFFICIENT_NEXT_STEPS,
            customer_draft=CUSTOMER_DRAFTS[CustomerReply.REQUEST_DETAILS],
            insufficient_context=True,
        )

    def get_incident(self, incident_id: int) -> Incident:
        incident = self._repository.get(incident_id)
        if incident is None:
            raise NotFoundError(f"Incident {incident_id} not found")
        return incident


def _parse_ndjson(text: str) -> tuple[list[Incident], list[dict[str, Any]]]:
    incidents: list[Incident] = []
    errors: list[dict[str, Any]] = []
    first_line_for_id: dict[int, int] = {}

    lines = [(n, line) for n, line in enumerate(text.splitlines(), start=1) if line.strip()]
    if not lines:
        errors.append({"line": None, "field": None, "message": "Body contains no incidents"})
    if len(lines) > MAX_INGEST_LINES:
        errors.append(
            {"line": None, "field": None, "message": f"At most {MAX_INGEST_LINES} lines allowed"}
        )
        return [], errors

    for line_no, line in lines:
        try:
            incident = Incident.model_validate(json.loads(line))
        except json.JSONDecodeError as exc:
            errors.append({"line": line_no, "field": None, "message": f"Invalid JSON: {exc.msg}"})
            continue
        except ValidationError as exc:
            errors.extend(
                {
                    "line": line_no,
                    "field": ".".join(str(p) for p in err["loc"]) or None,
                    "message": err["msg"],
                }
                for err in exc.errors()
            )
            continue
        if incident.id in first_line_for_id:
            errors.append(
                {
                    "line": line_no,
                    "field": "id",
                    "message": f"Duplicate id {incident.id} (first seen on line "
                    f"{first_line_for_id[incident.id]})",
                }
            )
            continue
        first_line_for_id[incident.id] = line_no
        incidents.append(incident)
    return incidents, errors

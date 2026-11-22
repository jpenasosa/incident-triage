"""Business logic for Incidents. The API layer delegates here; persistence is behind
IncidentRepository."""

import json
from typing import Any

import structlog
from pydantic import ValidationError

from incident_triage.assist import (
    ANALYSIS_SYSTEM_PROMPT,
    DRAFT_SYSTEM_PROMPT,
    INSUFFICIENT_CUSTOMER_DRAFT,
    INSUFFICIENT_NEXT_STEPS,
    SAFE_CUSTOMER_DRAFT,
    CustomerDraft,
    TriageAnalysis,
    apply_guardrail,
    build_analysis_input,
    build_draft_context,
    build_next_steps,
    dedupe_by_title,
    draft_rejection_reasons,
)
from incident_triage.errors import AppError, NotFoundError
from incident_triage.llm import LLMClient
from incident_triage.models import (
    AssistRequest,
    AssistResponse,
    CitedIncident,
    Incident,
    IngestResult,
    SearchPage,
    SearchQuery,
    SearchResult,
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

    def find_candidates(self, request: AssistRequest) -> list[SearchResult]:
        """Retrieve assist Candidates through the same search path as GET /search."""
        self._llm.ensure_available()  # fail fast: no search if assist can't finish
        query = SearchQuery(
            q=request.question,
            **request.model_dump(include={"environment", "service", "severity", "tags"}),
            limit=CANDIDATE_LIMIT,
        )
        log.info(
            "assist_step",
            step="tool:search",
            phase="input",
            q=query.q,
            filters=query.model_dump(include={"environment", "service", "severity", "tags"}),
            limit=query.limit,
        )
        page = self.search(query)
        candidates = page.results
        log.info(
            "assist_step",
            step="tool:search",
            phase="output",
            hits=page.total,
            top_ids=[c.id for c in candidates],
        )
        log.debug(
            "assist_candidates",
            candidates=[
                {"id": c.id, "score": c.score, "service": c.service, "title": c.title}
                for c in candidates
            ],
        )
        return candidates

    def assist(self, request: AssistRequest) -> AssistResponse:
        candidates = self.find_candidates(request)
        if not candidates:  # nothing to ground an answer in: don't call the LLM
            return self._insufficient("no_candidates")

        shown = dedupe_by_title(candidates)
        records = [r for r in (self._repository.get(c.id) for c in shown) if r is not None]
        log.info(
            "assist_step",
            step="llm:analyze",
            phase="input",
            candidate_ids=[r.id for r in records],
            collapsed_duplicates=len(candidates) - len(shown),
        )
        analysis = self._llm.complete(
            system=ANALYSIS_SYSTEM_PROMPT,
            user=build_analysis_input(request.question, records),
            output=TriageAnalysis,
        )
        by_id = {r.id: r for r in records}
        guarded = apply_guardrail(analysis, shown=by_id)
        log.info(
            "assist_step",
            step="guardrail",
            selected_ids=[c.id for c in guarded.cited],
            dropped=guarded.dropped,
            insufficient=guarded.insufficient,
        )
        if guarded.insufficient:
            flagged = analysis.insufficient_context
            return self._insufficient("model_said_insufficient" if flagged else "none_verified")

        cited_records = [by_id[c.id] for c in guarded.cited]
        # Everything below except the draft comes from stored records or verified quotes.
        return AssistResponse(
            relevant_incidents=[
                CitedIncident(
                    id=c.id, title=by_id[c.id].title, reason=f"Record states: “{c.evidence}”"
                )
                for c in guarded.cited
            ],
            next_steps=build_next_steps(cited_records),
            customer_draft=self._customer_draft(analysis, cited_records),
            insufficient_context=False,
        )

    def _customer_draft(self, analysis: TriageAnalysis, cited_records: list[Incident]) -> str:
        """Call 2 (ADR 0003): sees only the allowlisted context; backstop on the output."""
        log.info("assist_step", step="llm:draft", phase="input", cited_count=len(cited_records))
        draft = self._llm.complete(
            system=DRAFT_SYSTEM_PROMPT,
            user=build_draft_context(analysis, cited_records),
            output=CustomerDraft,
        ).draft
        rejected = draft_rejection_reasons(draft)
        log.info("assist_step", step="llm:draft", phase="output", rejected=rejected)
        if rejected:
            log.warning("customer_draft_replaced", reasons=rejected)
            return SAFE_CUSTOMER_DRAFT
        return draft

    def _insufficient(self, reason: str) -> AssistResponse:
        log.info("assist_insufficient", reason=reason)
        return AssistResponse(
            relevant_incidents=[],
            next_steps=INSUFFICIENT_NEXT_STEPS,
            customer_draft=INSUFFICIENT_CUSTOMER_DRAFT,
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

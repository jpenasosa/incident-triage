"""API layer: thin routes that validate input and delegate to the service layer."""

from collections.abc import Iterator
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Request
from starlette.concurrency import run_in_threadpool

from incident_triage.db import connect
from incident_triage.errors import AppError
from incident_triage.models import (
    MAX_INCIDENT_ID,
    AssistRequest,
    AssistResponse,
    Incident,
    IngestResult,
    SearchPage,
    SearchQuery,
)
from incident_triage.repository import SqliteIncidentRepository
from incident_triage.service import IncidentService

MAX_INGEST_BYTES = 5 * 1024 * 1024
NDJSON_MEDIA_TYPES = {"application/x-ndjson", "application/jsonl"}

router = APIRouter()


class PayloadTooLargeError(AppError):
    status_code = 413
    code = "PAYLOAD_TOO_LARGE"


class UnsupportedMediaTypeError(AppError):
    status_code = 415
    code = "UNSUPPORTED_MEDIA_TYPE"


def get_service(request: Request) -> Iterator[IncidentService]:
    """One SQLite connection per request."""
    with connect(request.app.state.settings.DB_PATH) as conn:
        yield IncidentService(SqliteIncidentRepository(conn), request.app.state.llm)


ServiceDep = Annotated[IncidentService, Depends(get_service)]


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.post("/ingest")
async def ingest(request: Request, service: ServiceDep) -> IngestResult:
    # async so the body can be read with a size cap instead of buffered unbounded.
    media_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if media_type not in NDJSON_MEDIA_TYPES:
        raise UnsupportedMediaTypeError(
            "Send newline-delimited JSON with Content-Type: application/x-ndjson"
        )
    body = await _read_capped(request, MAX_INGEST_BYTES)
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise AppError("Body must be UTF-8") from exc
    return await run_in_threadpool(service.ingest_ndjson, text)


@router.get("/search")
def search(query: Annotated[SearchQuery, Query()], service: ServiceDep) -> SearchPage:
    return service.search(query)


# Placeholder until LLM analysis lands (ticket 06, step 2). Candidates are in the logs.
_CANNED_ASSIST = AssistResponse(
    relevant_incidents=[],
    next_steps=["Assist analysis is not implemented yet."],
    customer_draft="",
    insufficient_context=True,
)


@router.post("/assist")
def assist(request: AssistRequest, service: ServiceDep) -> AssistResponse:
    service.find_candidates(request)
    return _CANNED_ASSIST


@router.get("/incidents/{incident_id}")
def get_incident(
    incident_id: Annotated[int, Path(gt=0, le=MAX_INCIDENT_ID)], service: ServiceDep
) -> Incident:
    return service.get_incident(incident_id)


async def _read_capped(request: Request, limit: int) -> bytes:
    too_large = PayloadTooLargeError(f"Body exceeds {limit // (1024 * 1024)} MB")
    if int(request.headers.get("content-length") or 0) > limit:
        raise too_large
    chunks, size = [], 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise too_large
        chunks.append(chunk)
    return b"".join(chunks)

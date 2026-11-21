"""Structured logging and per-request correlation IDs."""

import logging
import re
import sys
import time
import uuid

import structlog
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from incident_triage.errors import error_response

CORRELATION_HEADER = "X-Correlation-ID"
# Accept caller-supplied IDs only if they are safe to echo and log.
_VALID_CORRELATION_ID = re.compile(r"^[A-Za-z0-9._-]{1,128}$")

log = structlog.get_logger()


def configure_logging(level: str) -> None:
    """JSON logs to stdout; correlation_id is merged in from contextvars."""
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.format_exc_info,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(level)),
        logger_factory=structlog.PrintLoggerFactory(sys.stdout),
        cache_logger_on_first_use=False,
    )


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Binds a correlation ID to every log line of a request and echoes it back.

    Also the last line of defence for unhandled exceptions: they are logged and
    turned into the standard error body here, so the response still carries the ID.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        supplied = request.headers.get(CORRELATION_HEADER, "")
        correlation_id = supplied if _VALID_CORRELATION_ID.match(supplied) else str(uuid.uuid4())

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(correlation_id=correlation_id)
        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            log.exception("unhandled_error")
            response = error_response(500, "INTERNAL_ERROR", "Internal server error")
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)

        log.info(
            "request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
        )
        response.headers[CORRELATION_HEADER] = correlation_id
        structlog.contextvars.clear_contextvars()
        return response

"""FastAPI application factory and server entry point."""

import structlog
import uvicorn
from fastapi import FastAPI

from incident_triage.config import Settings, load_settings
from incident_triage.errors import register_error_handlers
from incident_triage.observability import CorrelationIdMiddleware, configure_logging
from incident_triage.routes import router


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.LOG_LEVEL)

    # Warn if the LLM API key is not set, since /assist will fail fast without it.
    if settings.LLM_API_KEY is None:
        structlog.get_logger().warning(
            "llm_api_key_not_set",
            detail="/assist is unavailable until LLM_API_KEY is set (in .env or the environment)",
        )

    app = FastAPI(title="Incident Triage")
    app.state.settings = settings
    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app)
    app.include_router(router)
    return app


def main() -> None:
    """Run the server (`uv run start`)."""
    settings = load_settings()
    # Access logging is done by CorrelationIdMiddleware so each line carries the ID.
    uvicorn.run(create_app(settings), host="127.0.0.1", port=settings.PORT, access_log=False)

"""FastAPI application factory."""

from fastapi import FastAPI

from incident_triage.config import Settings, load_settings
from incident_triage.errors import register_error_handlers
from incident_triage.observability import CorrelationIdMiddleware, configure_logging


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings()
    configure_logging(settings.log_level)

    app = FastAPI(title="Incident Triage")
    app.state.settings = settings
    app.add_middleware(CorrelationIdMiddleware)
    register_error_handlers(app)

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app

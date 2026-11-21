"""Entry point: `uv run python -m incident_triage`."""

import sys

import uvicorn

from incident_triage.config import ConfigError, load_settings
from incident_triage.main import create_app


def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        sys.exit(str(exc))
    # Access logging is done by CorrelationIdMiddleware so each line carries the ID.
    uvicorn.run(create_app(settings), host="127.0.0.1", port=settings.port, access_log=False)


if __name__ == "__main__":
    main()

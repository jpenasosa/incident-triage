import pytest
from fastapi.testclient import TestClient

from incident_triage.config import Settings
from incident_triage.main import create_app


@pytest.fixture
def settings(tmp_path) -> Settings:
    return Settings(DB_PATH=tmp_path / "test.db", _env_file=None)


@pytest.fixture
def app(settings):
    return create_app(settings)


@pytest.fixture
def client(app) -> TestClient:
    # raise_server_exceptions=False so unhandled errors become 500 responses, as in production.
    return TestClient(app, raise_server_exceptions=False)

import json
import uuid

import pytest
from fastapi import Query

from incident_triage.config import Settings, load_settings
from incident_triage.main import create_app
from incident_triage.observability import CORRELATION_HEADER


def test_generates_correlation_id_when_absent(client):
    resp = client.get("/healthz")

    assert resp.status_code == 200
    uuid.UUID(resp.headers[CORRELATION_HEADER])  # raises if not a UUID


def test_echoes_supplied_correlation_id(client):
    resp = client.get("/healthz", headers={CORRELATION_HEADER: "abc-123"})

    assert resp.headers[CORRELATION_HEADER] == "abc-123"


def test_replaces_unsafe_correlation_id(client):
    resp = client.get("/healthz", headers={CORRELATION_HEADER: "bad id\nwith newline"})

    uuid.UUID(resp.headers[CORRELATION_HEADER])


def test_not_found_uses_error_shape(client):
    resp = client.get("/nope", headers={CORRELATION_HEADER: "cid-404"})

    assert resp.status_code == 404
    assert resp.json() == {
        "error": {"code": "NOT_FOUND", "message": "Not Found", "correlation_id": "cid-404"}
    }


def test_validation_error_uses_error_shape(app, client):
    @app.get("/_test/validate")
    def _validate(n: int = Query(ge=1)):
        return {"n": n}

    resp = client.get("/_test/validate?n=0", headers={CORRELATION_HEADER: "cid-422"})

    body = resp.json()["error"]
    assert resp.status_code == 422
    assert body["code"] == "VALIDATION_ERROR"
    assert body["correlation_id"] == "cid-422"
    assert body["details"][0]["field"] == "query.n"


def test_unhandled_error_is_500_without_leaking_internals(app, client):
    @app.get("/_test/boom")
    def _boom():
        raise RuntimeError("secret internal detail")

    resp = client.get("/_test/boom", headers={CORRELATION_HEADER: "cid-500"})

    assert resp.status_code == 500
    assert resp.headers[CORRELATION_HEADER] == "cid-500"
    assert resp.json()["error"] == {
        "code": "INTERNAL_ERROR",
        "message": "Internal server error",
        "correlation_id": "cid-500",
    }


def test_every_request_log_line_has_correlation_id(app, client, capsys):
    @app.get("/_test/log")
    def _log():
        import structlog

        structlog.get_logger().info("inside_handler")
        return {}

    client.get("/_test/log", headers={CORRELATION_HEADER: "cid-log"})

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert {line["event"] for line in lines} >= {"inside_handler", "request"}
    assert all(line["correlation_id"] == "cid-log" for line in lines)


def test_missing_api_key_logs_warning_but_app_starts(settings, capsys):
    create_app(settings)  # settings fixture has no LLM_API_KEY

    lines = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]
    assert any(
        line["event"] == "llm_api_key_not_set" and line["level"] == "warning" for line in lines
    )


def test_blank_api_key_counts_as_unset():
    assert Settings(LLM_API_KEY="  ", _env_file=None).LLM_API_KEY is None


def test_invalid_setting_exits_with_variable_name(monkeypatch, tmp_path):
    monkeypatch.setenv("PORT", "abc")
    monkeypatch.chdir(tmp_path)  # no .env file here

    with pytest.raises(SystemExit) as exc:
        load_settings()

    assert "Invalid configuration" in str(exc.value.code)
    assert "PORT" in str(exc.value.code)

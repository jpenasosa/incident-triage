from factories import incident_dict, ndjson
from factories import make_analysis as analysis
from fakes import FakeLLMClient

from incident_triage.llm import AssistUnavailable

NDJSON = {"Content-Type": "application/x-ndjson"}


def test_assist_returns_cited_incidents(app, client):
    app.state.llm = FakeLLMClient(analysis((1, "SSO login timeout")))
    client.post(
        "/ingest", content=ndjson(incident_dict(id=1, title="SSO login timeout")), headers=NDJSON
    )

    resp = client.post("/assist", json={"question": "sso login failing"})

    body = resp.json()
    assert resp.status_code == 200
    assert body["relevant_incidents"] == [
        {"id": 1, "title": "SSO login timeout", "reason": "Record states: “SSO login timeout”"}
    ]
    assert body["insufficient_context"] is False


def test_llm_error_is_503(app, client):
    app.state.llm = FakeLLMClient(AssistUnavailable("timed out after 20.0s"))
    client.post("/ingest", content=ndjson(incident_dict(id=1, title="SSO")), headers=NDJSON)

    resp = client.post("/assist", json={"question": "sso"})

    assert resp.status_code == 503
    assert "timed out" not in resp.text


def test_assist_without_key_is_503(client):  # the default test settings have no LLM_API_KEY
    resp = client.post("/assist", json={"question": "sso login failing"})

    assert resp.status_code == 503
    assert resp.json()["error"]["code"] == "ASSIST_UNAVAILABLE"
    assert resp.json()["error"]["message"] == "Assist service is not accessible"


def test_invalid_assist_requests_are_422(app, client):
    app.state.llm = FakeLLMClient()
    for body in [
        {},
        {"question": ""},
        {"question": "   "},
        {"question": " \n\t "},
        {"question": "   ", "environment": "prod"},  # must not become a filter-only search
        {"question": "x" * 2001},
        {"question": "x", "environment": "uat"},
        {"question": "x", "bogus": 1},
    ]:
        assert client.post("/assist", json=body).status_code == 422, body

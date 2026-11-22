from factories import incident_dict, ndjson
from fakes import FakeLLMClient

NDJSON = {"Content-Type": "application/x-ndjson"}


def test_assist_returns_canned_response_shape(app, client):
    app.state.llm = FakeLLMClient()
    client.post(
        "/ingest", content=ndjson(incident_dict(id=1, title="SSO login timeout")), headers=NDJSON
    )

    resp = client.post("/assist", json={"question": "sso login failing"})

    assert resp.status_code == 200
    assert set(resp.json()) == {
        "relevant_incidents",
        "next_steps",
        "customer_draft",
        "insufficient_context",
    }


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

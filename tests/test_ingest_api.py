from factories import incident_dict, ndjson

from incident_triage.routes import MAX_INGEST_BYTES

NDJSON = {"Content-Type": "application/x-ndjson"}


def test_ingest_is_idempotent_over_http(client):
    body = ndjson(incident_dict(id=1), incident_dict(id=2))

    first = client.post("/ingest", content=body, headers=NDJSON)
    second = client.post("/ingest", content=body, headers=NDJSON)

    assert first.json() == {"ingested": 2, "updated": 0, "skipped": 0}
    assert second.json() == {"ingested": 0, "updated": 0, "skipped": 2}


def test_invalid_line_returns_422_with_details(client):
    resp = client.post("/ingest", content=ndjson(incident_dict(severity="P1")), headers=NDJSON)

    error = resp.json()["error"]
    assert resp.status_code == 422
    assert error["code"] == "INVALID_INCIDENTS"
    assert error["details"][0]["line"] == 1
    assert error["details"][0]["field"] == "severity"


def test_wrong_content_type_returns_415(client):
    resp = client.post("/ingest", json=[incident_dict()])

    assert resp.status_code == 415


def test_oversized_body_returns_413(client):
    resp = client.post("/ingest", content=b"x" * (MAX_INGEST_BYTES + 1), headers=NDJSON)

    assert resp.status_code == 413

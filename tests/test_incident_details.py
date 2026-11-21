import pytest
from factories import incident_dict, make_incident, ndjson
from fakes import FakeIncidentRepository

from incident_triage.errors import NotFoundError
from incident_triage.service import IncidentService

NDJSON = {"Content-Type": "application/x-ndjson"}


def test_service_returns_the_incident():
    incident = make_incident(id=1)
    service = IncidentService(FakeIncidentRepository(incidents=[incident]))

    assert service.get_incident(1) == incident


def test_service_raises_not_found_for_unknown_id():
    service = IncidentService(FakeIncidentRepository())

    with pytest.raises(NotFoundError, match="Incident 404 not found"):
        service.get_incident(404)


def test_api_returns_full_record_as_ingested(client):
    row = incident_dict(id=42, tags=["db", "timeout"])
    client.post("/ingest", content=ndjson(row), headers=NDJSON)

    resp = client.get("/incidents/42")

    assert resp.status_code == 200
    assert resp.json() == row


def test_api_unknown_id_is_404_in_error_shape(client):
    resp = client.get("/incidents/99999", headers={"X-Correlation-ID": "cid-1"})

    assert resp.status_code == 404
    assert resp.json() == {
        "error": {
            "code": "NOT_FOUND",
            "message": "Incident 99999 not found",
            "correlation_id": "cid-1",
        }
    }


def test_api_malformed_id_is_422(client):
    resp = client.get("/incidents/abc")

    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "VALIDATION_ERROR"


def test_api_out_of_range_id_is_422(client):
    assert client.get("/incidents/0").status_code == 422
    assert client.get(f"/incidents/{2**63}").status_code == 422

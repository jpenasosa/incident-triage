import pytest
from factories import incident_dict, ndjson
from fakes import FakeIncidentRepository, FakeLLMClient

from incident_triage.models import IngestResult
from incident_triage.service import MAX_INGEST_LINES, IncidentService, InvalidIngestError


def ingest(text: str, repo: FakeIncidentRepository | None = None):
    repo = repo or FakeIncidentRepository()
    return IncidentService(repo, FakeLLMClient()).ingest_ndjson(text), repo


def errors_of(text: str) -> list[dict]:
    repo = FakeIncidentRepository()
    with pytest.raises(InvalidIngestError) as exc:
        IncidentService(repo, FakeLLMClient()).ingest_ndjson(text)
    assert repo.upserted is None, "nothing may be written when any line is invalid"
    return exc.value.details


def test_valid_batch_is_passed_to_repository_and_its_result_returned():
    repo = FakeIncidentRepository(IngestResult(ingested=2))
    text = ndjson(incident_dict(id=1), incident_dict(id=2))

    result, repo = ingest(text, repo)

    assert result == IngestResult(ingested=2)
    assert [i.id for i in repo.upserted] == [1, 2]


def test_blank_lines_are_ignored():
    _, repo = ingest("\n" + ndjson(incident_dict()) + "\n\n")

    assert len(repo.upserted) == 1


def test_tags_are_normalised():
    _, repo = ingest(ndjson(incident_dict(tags=["DB", " timeout ", "db"])))

    assert repo.upserted[0].tags == ["db", "timeout"]


def test_invalid_json_reports_line_number():
    text = ndjson(incident_dict(id=1)) + "{not json\n"

    assert errors_of(text) == [
        {
            "line": 2,
            "field": None,
            "message": "Invalid JSON: Expecting property name enclosed in double quotes",
        }
    ]


def test_schema_errors_report_line_and_field():
    text = ndjson(incident_dict(id=1), incident_dict(id=2, severity="P1", environment="uat"))

    errs = errors_of(text)

    assert {(e["line"], e["field"]) for e in errs} == {(2, "environment"), (2, "severity")}


def test_duplicate_id_within_batch_is_rejected():
    text = ndjson(incident_dict(id=1), incident_dict(id=1))

    assert errors_of(text) == [
        {"line": 2, "field": "id", "message": "Duplicate id 1 (first seen on line 1)"}
    ]


def test_empty_body_is_rejected():
    assert errors_of("\n\n")[0]["message"] == "Body contains no incidents"


def test_too_many_lines_is_rejected():
    text = ndjson(*(incident_dict(id=n + 1) for n in range(MAX_INGEST_LINES + 1)))

    assert "At most" in errors_of(text)[0]["message"]


@pytest.mark.parametrize("bad_id", ["42", 0, -1, 1.5, True, 2**63])
def test_id_must_be_a_positive_64_bit_integer(bad_id):
    errs = errors_of(ndjson(incident_dict(id=bad_id)))

    assert errs[0]["field"] == "id"

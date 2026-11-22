import json

import pytest
from factories import make_result
from fakes import FakeIncidentRepository, FakeLLMClient
from pydantic import ValidationError

from incident_triage.llm import AssistUnavailable
from incident_triage.models import AssistRequest
from incident_triage.service import CANDIDATE_LIMIT, IncidentService


def logs(capsys) -> list[dict]:
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


def test_missing_llm_fails_fast_without_searching():
    repo = FakeIncidentRepository(search_results=[make_result(1)])
    service = IncidentService(repo, FakeLLMClient(available=False))

    with pytest.raises(AssistUnavailable):
        service.find_candidates(AssistRequest(question="sso login failing"))

    assert repo.searched is None


def test_candidates_come_from_search_with_question_filters_and_limit():
    repo = FakeIncidentRepository(search_results=[make_result(n) for n in range(1, 15)])
    request = AssistRequest(question="SSO login failing", environment="prod", tags=["sso"])

    candidates = IncidentService(repo, FakeLLMClient()).find_candidates(request)

    assert [c.id for c in candidates] == list(range(1, CANDIDATE_LIMIT + 1))
    assert repo.searched.q == "SSO login failing"
    assert repo.searched.environment == "prod"
    assert repo.searched.tags == ["sso"]
    assert repo.searched.limit == CANDIDATE_LIMIT


def test_tool_search_steps_are_logged(capsys):
    repo = FakeIncidentRepository(search_results=[make_result(7), make_result(3)])

    IncidentService(repo, FakeLLMClient()).find_candidates(
        AssistRequest(question="timeout", service="reporting")
    )

    steps = [e for e in logs(capsys) if e["event"] == "assist_step"]
    assert [(s["step"], s["phase"]) for s in steps] == [
        ("tool:search", "input"),
        ("tool:search", "output"),
    ]
    assert steps[0]["q"] == "timeout"
    assert steps[0]["filters"]["service"] == "reporting"
    assert (steps[1]["hits"], steps[1]["top_ids"]) == (2, [7, 3])


def test_candidate_details_are_logged_at_debug_only(capsys):
    from incident_triage.observability import configure_logging

    repo = FakeIncidentRepository(search_results=[make_result(7)])
    service = IncidentService(repo, FakeLLMClient())

    service.find_candidates(AssistRequest(question="timeout"))  # INFO (autouse default)
    assert not any(e["event"] == "assist_candidates" for e in logs(capsys))

    configure_logging("DEBUG")
    service.find_candidates(AssistRequest(question="timeout"))
    debug = [e for e in logs(capsys) if e["event"] == "assist_candidates"]
    assert debug[0]["candidates"][0]["id"] == 7


def test_question_is_stripped_and_must_not_be_blank():
    assert AssistRequest(question="  sso login  ").question == "sso login"
    with pytest.raises(ValidationError):
        AssistRequest(question="   ", environment="prod")

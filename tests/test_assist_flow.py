"""IncidentService.assist() end to end with the fake repository and fake LLM."""

import json

import pytest
from factories import make_analysis as analysis
from factories import make_draft, make_incident, make_result
from fakes import FakeIncidentRepository, FakeLLMClient

from incident_triage.assist import (
    INSUFFICIENT_CUSTOMER_DRAFT,
    INSUFFICIENT_NEXT_STEPS,
    SAFE_CUSTOMER_DRAFT,
    STANDARD_NEXT_STEPS,
    CustomerDraft,
    TriageAnalysis,
)
from incident_triage.llm import AssistUnavailable
from incident_triage.models import AssistRequest
from incident_triage.service import IncidentService

INCIDENTS = [
    make_incident(
        id=1,
        title="SSO login failing",
        description="SSO logins failed with an invalid assertion error.",
        resolution_summary="Rotated the expired signing certificate.",
    ),
    make_incident(id=2, title="SSO login failing"),  # duplicate title of #1
    make_incident(
        id=3,
        title="Password reset emails missing",
        resolution_summary="Raised the email provider rate limit.",
    ),
]
SSO_EVIDENCE = "failed with an invalid assertion error"
RESET_EVIDENCE = "Password reset emails missing"


def repo():
    return FakeIncidentRepository(
        incidents=INCIDENTS, search_results=[make_result(i.id, title=i.title) for i in INCIDENTS]
    )


def run(llm):
    return IncidentService(repo(), llm).assist(AssistRequest(question="SSO broken"))


def logs(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


def test_zero_candidates_returns_insufficient_without_calling_the_llm():
    llm = FakeLLMClient()

    result = IncidentService(FakeIncidentRepository(), llm).assist(AssistRequest(question="x"))

    assert result.insufficient_context is True
    assert result.next_steps == INSUFFICIENT_NEXT_STEPS
    assert result.customer_draft == INSUFFICIENT_CUSTOMER_DRAFT
    assert llm.calls == []


def test_llm_sees_question_and_full_records_of_deduplicated_candidates():
    llm = FakeLLMClient(analysis((1, SSO_EVIDENCE)), make_draft())

    run(llm)

    call = llm.calls[0]
    assert call["output"] is TriageAnalysis
    assert "SSO broken" in call["user"]
    assert "Rotated the expired signing certificate." in call["user"]  # full record
    assert '"id": 1' in call["user"] and '"id": 3' in call["user"]
    assert '"id": 2' not in call["user"]  # duplicate title collapsed


def test_response_contains_only_stored_or_verified_text():
    llm = FakeLLMClient(analysis((3, RESET_EVIDENCE), (1, SSO_EVIDENCE)), make_draft("All good."))

    result = run(llm)

    assert [(c.id, c.title, c.reason) for c in result.relevant_incidents] == [
        (3, "Password reset emails missing", f"Record states: “{RESET_EVIDENCE}”"),
        (1, "SSO login failing", f"Record states: “{SSO_EVIDENCE}”"),
    ]
    assert result.next_steps == [
        "Incident 3 was resolved by: Raised the email provider rate limit.",
        "Incident 1 was resolved by: Rotated the expired signing certificate.",
        *STANDARD_NEXT_STEPS,
    ]
    assert result.customer_draft == "All good."
    assert result.insufficient_context is False


def test_invented_unshown_and_unverifiable_citations_are_dropped(capsys):
    # 42 doesn't exist; 2 was collapsed so the model never saw it; 3's evidence is invented.
    llm = FakeLLMClient(
        analysis(
            (42, SSO_EVIDENCE),
            (2, SSO_EVIDENCE),
            (3, "caused by a DNS outage at the provider"),
            (1, SSO_EVIDENCE),
        ),
        make_draft(),
    )

    result = run(llm)

    assert [c.id for c in result.relevant_incidents] == [1]
    step = next(e for e in logs(capsys) if e.get("step") == "guardrail")
    assert step["selected_ids"] == [1]
    assert step["dropped"] == [
        {"id": 42, "why": "not_shown"},
        {"id": 2, "why": "not_shown"},
        {"id": 3, "why": "evidence_not_found"},
    ]


def test_nothing_verified_gives_insufficient_response():
    result = run(FakeLLMClient(analysis((1, "paraphrased claim not in the record"))))

    assert result.insufficient_context is True
    assert result.relevant_incidents == []
    assert result.next_steps == INSUFFICIENT_NEXT_STEPS


def test_model_reporting_insufficient_context_gives_insufficient_response():
    assert run(FakeLLMClient(analysis(insufficient=True))).insufficient_context is True


def test_assist_steps_are_logged_in_order(capsys):
    run(FakeLLMClient(analysis((1, SSO_EVIDENCE)), make_draft()))

    steps = [(e["step"], e.get("phase")) for e in logs(capsys) if e["event"] == "assist_step"]
    assert steps == [
        ("tool:search", "input"),
        ("tool:search", "output"),
        ("llm:analyze", "input"),
        ("guardrail", None),
        ("llm:draft", "input"),
        ("llm:draft", "output"),
    ]


def test_llm_failure_propagates_as_assist_unavailable():
    with pytest.raises(AssistUnavailable):
        run(FakeLLMClient(AssistUnavailable("timed out")))


# --- Customer Draft (call 2) ---------------------------------------------------------


def test_draft_call_sees_only_the_allowlisted_context():
    llm = FakeLLMClient(analysis((3, RESET_EVIDENCE)), make_draft())

    run(llm)  # question "SSO broken"; 1 and 3 shown, only 3 cited

    draft_call = llm.calls[1]
    context = json.loads(draft_call["user"])
    assert draft_call["output"] is CustomerDraft
    assert set(context) == {
        "customer_status",
        "impact_category",
        "customer_action_needed",
        "related_past_incidents",
    }
    assert context["related_past_incidents"] == [
        {
            "service": "payroll",
            "severity": "SEV2",
            "title": "Password reset emails missing",
            "description": "The payroll run did not complete in time.",
            "resolution_summary": "Raised the email provider rate limit.",
        }
    ]
    assert "SSO broken" not in draft_call["user"]  # no engineer question
    assert "SSO login failing" not in draft_call["user"]  # no uncited Candidate
    assert '"id"' not in draft_call["user"]  # no Incident ids


@pytest.mark.parametrize(
    "leaky",
    [
        "Please check db-prod-03.internal for the error.",
        "The server at 10.2.3.4 has been restarted.",
        "Contact oncall@acme-hr.com for help.",
        "See https://status.example for updates.",
        "Updates will be posted on status.example.ca shortly.",
        "The node 2001:db8::8a2e:370:7334 was restarted.",
        "We are on it. " * 60,  # over MAX_DRAFT_WORDS
    ],
)
def test_backstop_replaces_drafts_with_internal_identifiers(leaky, capsys):
    result = run(FakeLLMClient(analysis((1, SSO_EVIDENCE)), make_draft(leaky)))

    assert result.customer_draft == SAFE_CUSTOMER_DRAFT
    assert any(e["event"] == "customer_draft_replaced" for e in logs(capsys))


def test_insufficient_paths_never_call_the_draft_model():
    llm = FakeLLMClient(analysis(insufficient=True))

    result = run(llm)

    assert len(llm.calls) == 1  # analysis only
    assert result.customer_draft == INSUFFICIENT_CUSTOMER_DRAFT


def test_draft_call_failure_propagates_as_assist_unavailable():
    llm = FakeLLMClient(analysis((1, SSO_EVIDENCE)), AssistUnavailable("timed out"))

    with pytest.raises(AssistUnavailable):
        run(llm)

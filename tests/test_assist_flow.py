"""IncidentService.assist() end to end with the fake repository and fake LLM."""

import json
import re

import pytest
from factories import make_analysis as analysis
from factories import make_incident, make_result
from fakes import FakeIncidentRepository, FakeLLMClient

from incident_triage.assist import (
    ANALYSIS_SYSTEM_PROMPT,
    CUSTOMER_DRAFTS,
    INSUFFICIENT_NEXT_STEPS,
    STANDARD_NEXT_STEPS,
    TriageAnalysis,
)
from incident_triage.llm import AssistUnavailable
from incident_triage.models import AssistRequest, CustomerReply
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
    assert result.customer_draft == CUSTOMER_DRAFTS[CustomerReply.REQUEST_DETAILS]
    assert llm.calls == []


def test_llm_sees_question_and_full_records_of_deduplicated_candidates():
    llm = FakeLLMClient(analysis((1, SSO_EVIDENCE)))

    run(llm)

    call = llm.calls[0]
    assert call["output"] is TriageAnalysis
    assert "SSO broken" in call["user"]
    assert "Rotated the expired signing certificate." in call["user"]  # full record
    assert '"id": 1' in call["user"] and '"id": 3' in call["user"]
    assert '"id": 2' not in call["user"]  # duplicate title collapsed


def test_response_contains_only_stored_or_verified_text():
    llm = FakeLLMClient(analysis((3, RESET_EVIDENCE), (1, SSO_EVIDENCE)))

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
    assert result.customer_draft == CUSTOMER_DRAFTS[CustomerReply.INVESTIGATING]
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
    run(FakeLLMClient(analysis((1, SSO_EVIDENCE))))

    steps = [(e["step"], e.get("phase")) for e in logs(capsys) if e["event"] == "assist_step"]
    assert steps == [
        ("tool:search", "input"),
        ("tool:search", "output"),
        ("llm:analyze", "input"),
        ("guardrail", None),
        ("draft", None),
    ]


def test_llm_failure_propagates_as_assist_unavailable():
    with pytest.raises(AssistUnavailable):
        run(FakeLLMClient(AssistUnavailable("llm_timeout", "timed out")))


# --- Customer Draft (ADR 0006) ---------------------------------------------------------


@pytest.mark.parametrize("reply", list(CustomerReply))
def test_draft_is_the_template_for_the_chosen_reply_with_one_llm_call(reply, capsys):
    llm = FakeLLMClient(analysis((1, SSO_EVIDENCE), reply=reply))

    result = run(llm)

    assert result.customer_draft == CUSTOMER_DRAFTS[reply]
    assert len(llm.calls) == 1  # analysis only: no model writes customer text
    step = next(e for e in logs(capsys) if e.get("step") == "draft")
    assert (step["customer_reply"], step["source"]) == (reply.value, "template")


@pytest.mark.parametrize(
    "llm",
    [
        lambda: FakeLLMClient(analysis(insufficient=True, reply="fix_delivered")),
        lambda: FakeLLMClient(analysis((1, "not in the record at all"), reply="fix_delivered")),
    ],
    ids=["model_said_insufficient", "none_verified"],
)
def test_insufficient_paths_always_request_details(llm):
    result = run(llm())

    assert result.customer_draft == CUSTOMER_DRAFTS[CustomerReply.REQUEST_DETAILS]


def test_templates_cover_every_reply_and_contain_no_specifics():
    assert set(CUSTOMER_DRAFTS) == set(CustomerReply)
    for reply, text in CUSTOMER_DRAFTS.items():
        assert 0 < len(text.split()) <= 120, reply  # the brief asks for a short draft
        assert not re.search(r"\d|://|@", text), reply  # no numbers, links or addresses


def test_prompt_only_allows_fix_replies_when_the_question_says_so():
    assert "fix_delivered: only if the question says" in ANALYSIS_SYSTEM_PROMPT
    assert "A past incident's resolution is not evidence" in ANALYSIS_SYSTEM_PROMPT

"""IncidentService.assist() end to end with the fake repository and fake LLM."""

import json
import re

import pytest
from factories import make_analysis as analysis
from factories import make_incident, make_result
from fakes import FakeIncidentRepository, FakeLLMClient, search

from incident_triage.assist import (
    ANALYSIS_SYSTEM_PROMPT,
    CUSTOMER_DRAFTS,
    INSUFFICIENT_NEXT_STEPS,
    MAX_TOOL_CALLS,
    STANDARD_NEXT_STEPS,
    TriageAnalysis,
)
from incident_triage.llm import AssistUnavailable
from incident_triage.models import AssistRequest, CustomerReply
from incident_triage.service import CANDIDATE_LIMIT, IncidentService

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


def repo(search_results=None):
    every = [make_result(i.id, title=i.title) for i in INCIDENTS]
    return FakeIncidentRepository(incidents=INCIDENTS, search_results=search_results or every)


def run(llm, repository=None, **request):
    request = {"question": "SSO broken", **request}
    return IncidentService(repository or repo(), llm).assist(AssistRequest(**request))


def logs(capsys):
    return [json.loads(line) for line in capsys.readouterr().out.splitlines() if line]


def steps(capsys):
    return [e for e in logs(capsys) if e["event"] == "assist_step"]


# --- search_incidents tool ---------------------------------------------------------------


def test_model_gets_the_question_and_engineer_filters_and_a_capped_tool():
    llm = FakeLLMClient(search("sso"), analysis((1, SSO_EVIDENCE)))

    run(llm, environment="prod")

    call = llm.calls[0]
    assert call["output"] is TriageAnalysis
    assert call["max_tool_calls"] == MAX_TOOL_CALLS
    assert "SSO broken" in call["user"]
    assert '{"environment": "prod"}' in call["user"]


def test_tool_runs_the_search_path_and_returns_full_deduplicated_records():
    repository = repo()
    llm = FakeLLMClient(search("invalid assertion"), analysis((1, SSO_EVIDENCE)))

    run(llm, repository)

    assert repository.searched.q == "invalid assertion"  # the model's query, not the question
    assert repository.searched.limit == CANDIDATE_LIMIT
    output = json.loads(llm.calls[0].tool_outputs[0])
    assert output["total"] == 3
    assert [r["id"] for r in output["incidents"]] == [1, 3]  # duplicate title #2 collapsed
    assert (
        output["incidents"][0]["resolution_summary"] == "Rotated the expired signing certificate."
    )


def test_engineer_filters_override_the_models_and_the_model_can_narrow(capsys):
    repository = repo()
    llm = FakeLLMClient(
        search("sso", environment="dev", service="auth", tags=["login"]),
        analysis((1, SSO_EVIDENCE)),
    )

    run(llm, repository, environment="prod", tags=["sso"])

    executed = repository.searched
    assert (executed.environment, executed.service, executed.tags) == ("prod", "auth", ["sso"])
    step = next(s for s in steps(capsys) if s["step"] == "tool:search")
    assert step["overridden"] == ["environment", "tags"]
    assert step["filters"]["service"] == "auth"


def test_citations_are_checked_against_every_search_the_model_ran(capsys):
    by_q = {
        "sso": [make_result(1, title="SSO login failing")],
        "password reset": [make_result(3, title="Password reset emails missing")],
    }
    repository = repo(search_results=lambda query: by_q.get(query.q, []))
    llm = FakeLLMClient(
        search("sso"),
        search("password reset"),
        analysis((3, RESET_EVIDENCE), (1, SSO_EVIDENCE), (2, SSO_EVIDENCE)),
    )

    result = run(llm, repository)

    assert [c.id for c in result.relevant_incidents] == [3, 1]
    guardrail = next(s for s in steps(capsys) if s["step"] == "guardrail")
    assert guardrail["shown_ids"] == [1, 3]
    assert guardrail["dropped"] == [{"id": 2, "why": "not_shown"}]  # no search returned it


@pytest.mark.parametrize(
    "call",
    [search("sso", tags=["Not A Tag!"]), search("   "), search("x" * 501)],
    ids=["bad_tag", "blank_q_no_filters", "q_too_long"],
)
def test_invalid_tool_arguments_go_back_to_the_model_as_an_error(call, capsys):
    llm = FakeLLMClient(call, analysis(insufficient=True))

    result = run(llm)

    assert "error" in json.loads(llm.calls[0].tool_outputs[0])
    assert result.insufficient_context is True  # the request still completes
    assert any(s.get("error") for s in steps(capsys) if s["step"] == "tool:search")


def test_no_hits_from_any_search_gives_insufficient(capsys):
    llm = FakeLLMClient(search("sso"), search("login"), analysis(insufficient=True))

    result = run(llm, repo(search_results=lambda query: []))

    assert result.insufficient_context is True
    assert result.customer_draft == CUSTOMER_DRAFTS[CustomerReply.REQUEST_DETAILS]
    events = logs(capsys)
    assert (
        next(e for e in events if e["event"] == "assist_insufficient")["reason"] == "no_candidates"
    )
    draft = next(e for e in events if e.get("step") == "draft")  # logged on every path
    assert draft["customer_reply"] == "request_details"


def test_missing_llm_fails_fast_without_searching():
    repository = repo()

    with pytest.raises(AssistUnavailable):
        run(FakeLLMClient(available=False), repository)

    assert repository.searches == []


def test_candidate_details_are_logged_at_debug_only(capsys):
    from incident_triage.observability import configure_logging

    run(FakeLLMClient(search("sso"), analysis((1, SSO_EVIDENCE))))  # INFO (autouse default)
    assert not any(e["event"] == "assist_candidates" for e in logs(capsys))

    configure_logging("DEBUG")
    run(FakeLLMClient(search("sso"), analysis((1, SSO_EVIDENCE))))
    debug = [e for e in logs(capsys) if e["event"] == "assist_candidates"]
    assert [c["id"] for c in debug[0]["candidates"]] == [1, 2, 3]


# --- analysis and guardrail ------------------------------------------------------------


def test_response_contains_only_stored_or_verified_text():
    llm = FakeLLMClient(search("sso"), analysis((3, RESET_EVIDENCE), (1, SSO_EVIDENCE)))

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
    assert result.customer_draft == CUSTOMER_DRAFTS[CustomerReply.ACKNOWLEDGED]
    assert result.insufficient_context is False


def test_invented_unshown_and_unverifiable_citations_are_dropped(capsys):
    # 42 doesn't exist; 2 was collapsed so the model never saw it; 3's evidence is invented.
    llm = FakeLLMClient(
        search("sso"),
        analysis(
            (42, SSO_EVIDENCE),
            (2, SSO_EVIDENCE),
            (3, "caused by a DNS outage at the provider"),
            (1, SSO_EVIDENCE),
        ),
    )

    result = run(llm)

    assert [c.id for c in result.relevant_incidents] == [1]
    step = next(s for s in steps(capsys) if s["step"] == "guardrail")
    assert step["selected_ids"] == [1]
    assert step["dropped"] == [
        {"id": 42, "why": "not_shown"},
        {"id": 2, "why": "not_shown"},
        {"id": 3, "why": "evidence_not_found"},
    ]


def test_nothing_verified_gives_insufficient_response():
    llm = FakeLLMClient(search("sso"), analysis((1, "paraphrased claim not in the record")))

    result = run(llm)

    assert result.insufficient_context is True
    assert result.relevant_incidents == []
    assert result.next_steps == INSUFFICIENT_NEXT_STEPS


def test_model_reporting_insufficient_context_gives_insufficient_response():
    llm = FakeLLMClient(search("sso"), analysis(insufficient=True))

    assert run(llm).insufficient_context is True


def test_assist_steps_are_logged_in_order(capsys):
    run(FakeLLMClient(search("sso"), search("login"), analysis((1, SSO_EVIDENCE))))

    assert [(s["step"], s.get("phase")) for s in steps(capsys)] == [
        ("llm:analyze", "input"),
        ("tool:search", "input"),
        ("tool:search", "output"),
        ("tool:search", "input"),
        ("tool:search", "output"),
        ("guardrail", None),
        ("draft", None),
    ]


def test_llm_failure_propagates_as_assist_unavailable():
    with pytest.raises(AssistUnavailable):
        run(FakeLLMClient(search("sso"), AssistUnavailable("llm_timeout", "timed out")))


# --- Customer Draft (ADR 0006) ---------------------------------------------------------


@pytest.mark.parametrize("reply", list(CustomerReply))
def test_draft_is_the_template_for_the_chosen_reply(reply, capsys):
    llm = FakeLLMClient(search("sso"), analysis((1, SSO_EVIDENCE), reply=reply))

    result = run(llm)

    assert result.customer_draft == CUSTOMER_DRAFTS[reply]
    assert len(llm.calls) == 1  # one analysis run: no model writes customer text
    step = next(s for s in steps(capsys) if s["step"] == "draft")
    assert (step["customer_reply"], step["source"]) == (reply.value, "template")


@pytest.mark.parametrize(
    "replies",
    [
        (search("sso"), analysis(insufficient=True, reply="fix_delivered")),
        (search("sso"), analysis((1, "not in the record at all"), reply="fix_delivered")),
    ],
    ids=["model_said_insufficient", "none_verified"],
)
def test_insufficient_paths_always_request_details(replies):
    result = run(FakeLLMClient(*replies))

    assert result.customer_draft == CUSTOMER_DRAFTS[CustomerReply.REQUEST_DETAILS]


def test_templates_cover_every_reply_and_contain_no_specifics():
    assert set(CUSTOMER_DRAFTS) == set(CustomerReply)
    for reply, text in CUSTOMER_DRAFTS.items():
        assert 0 < len(text.split()) <= 120, reply  # the brief asks for a short draft
        assert not re.search(r"\d|://|@", text), reply  # no numbers, links or addresses


def test_prompt_only_allows_status_claims_when_the_question_says_so():
    for reply in ("fix_delivered", "fix_in_progress", "investigating"):
        assert f"{reply}: only if the question says" in ANALYSIS_SYSTEM_PROMPT
    assert "acknowledged: anything else" in ANALYSIS_SYSTEM_PROMPT  # neutral default
    assert "A past incident's resolution is not evidence" in ANALYSIS_SYSTEM_PROMPT

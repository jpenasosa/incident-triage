import pytest
from factories import make_analysis as analysis
from factories import make_incident, make_result

from incident_triage.assist import (
    MAX_CITATIONS,
    MAX_DRAFT_WORDS,
    STANDARD_NEXT_STEPS,
    apply_guardrail,
    build_next_steps,
    dedupe_by_title,
    draft_rejection_reasons,
    evidence_is_verbatim,
    internal_identifier_matches,
)

SSO = make_incident(
    id=1,
    title="Users unable to log in via single sign-on",
    description="SSO logins failed with an 'invalid assertion' error for 87 minutes.",
    resolution_summary="An expired signing certificate was rotated.",
)
RESET = make_incident(id=2, title="Password reset emails not arriving")
SHOWN = {1: SSO, 2: RESET}


@pytest.mark.parametrize(
    "evidence",
    [
        "failed with an 'invalid assertion' error",  # from the description
        "An expired signing certificate was rotated.",  # whole resolution
        "  USERS unable to   log in  ",  # case and whitespace are ignored
        "“expired signing certificate”",  # surrounding quotes are ignored
    ],
)
def test_verbatim_evidence_is_accepted(evidence):
    assert evidence_is_verbatim(evidence, SSO)


@pytest.mark.parametrize(
    "evidence",
    [
        "The certificate expired because of a cron misconfiguration",  # invented detail
        "SSO logins failed for 120 minutes",  # altered number
        "invalid assertion",  # too short to be meaningful evidence
        "single sign-on ... certificate was rotated",  # stitched / elided
        "Password reset emails not arriving",  # true, but from another incident
    ],
)
def test_paraphrased_invented_or_borrowed_evidence_is_rejected(evidence):
    assert not evidence_is_verbatim(evidence, SSO)


def test_citations_of_unshown_ids_or_unverifiable_evidence_are_dropped():
    guarded = apply_guardrail(
        analysis(
            (1, "expired signing certificate was rotated"),
            (99, "expired signing certificate was rotated"),
            (2, "emails were lost due to a DNS outage"),
        ),
        shown=SHOWN,
    )

    assert [c.id for c in guarded.cited] == [1]
    assert guarded.dropped == [
        {"id": 99, "why": "not_shown"},
        {"id": 2, "why": "evidence_not_found"},
    ]
    assert guarded.insufficient is False


def test_duplicate_citations_are_collapsed_and_capped():
    shown = {i: make_incident(id=i, title=f"Payroll run delayed number {i}") for i in range(1, 10)}
    cites = [(1, "Payroll run delayed")] + [(i, "Payroll run delayed") for i in range(1, 8)]

    guarded = apply_guardrail(analysis(*cites), shown=shown)

    assert [c.id for c in guarded.cited] == [1, 2, 3, 4, 5]
    assert len(guarded.cited) == MAX_CITATIONS


def test_nothing_verified_is_insufficient():
    guarded = apply_guardrail(analysis((1, "made-up words entirely here")), shown=SHOWN)

    assert guarded.insufficient is True
    assert guarded.cited == []


def test_model_insufficient_flag_wins_even_with_valid_citations():
    guarded = apply_guardrail(
        analysis((1, "expired signing certificate was rotated"), insufficient=True), shown=SHOWN
    )

    assert guarded.insufficient is True
    assert guarded.cited == []


def test_next_steps_quote_stored_resolutions_then_fixed_steps():
    twin = make_incident(id=3, resolution_summary=SSO.resolution_summary)

    steps = build_next_steps([SSO, twin])

    assert steps == [
        "Incident 1 was resolved by: An expired signing certificate was rotated.",
        *STANDARD_NEXT_STEPS,
    ]  # identical resolutions listed once


def test_dedupe_keeps_best_ranked_per_title():
    candidates = [
        make_result(3, title="SSO login failing"),
        make_result(1, title="sso login failing"),
        make_result(2, title="Payroll run delayed"),
    ]

    assert [c.id for c in dedupe_by_title(candidates)] == [3, 2]


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("We restarted db-prod-03.internal.", ["hostname"]),
        ("Check status.example.ca for updates", ["hostname"]),
        ("Logs are on intranet.example.uk now", ["hostname"]),
        ("Email ops@corp.example.com", ["email", "hostname"]),
        ("Details at http://intranet/page", ["url"]),
        ("Server 10.0.0.12 was patched.", ["ip_address"]),
        ("Reach it at 10.0.0.12:8443 instead", ["ip_address"]),
        ("The node 2001:db8::8a2e:370:7334 rebooted", ["ip_address"]),
        ("Bound to [fe80::1%eth0] on start", ["ip_address"]),
        ("Loopback ::1 was refused", ["ip_address"]),
    ],
)
def test_backstop_detects_internal_identifiers(text, expected):
    assert internal_identifier_matches(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "Our team rotated an expired certificate and logins are working again.",
        "Version 2.3 shipped; about 1.5x overtime was corrected.",
        "We expect an update by 10:30 a.m. (U.S. Eastern), e.g. via email.",
        "SEV2 impact on login access: no action is needed from you.",
    ],
)
def test_backstop_ignores_ordinary_customer_text(text):
    assert internal_identifier_matches(text) == []


@pytest.mark.parametrize(
    ("draft", "expected"),
    [
        ("word " * MAX_DRAFT_WORDS, []),  # exactly at the limit is fine
        ("word " * (MAX_DRAFT_WORDS + 1), ["too_long"]),
        ("   ", ["empty"]),
        ("See status.example.ca. " + "word " * MAX_DRAFT_WORDS, ["hostname", "too_long"]),
    ],
)
def test_draft_rejection_reasons(draft, expected):
    assert draft_rejection_reasons(draft) == expected

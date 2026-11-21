import pytest

from incident_triage.query import build_match_query


@pytest.mark.parametrize(
    ("q", "expected"),
    [
        ("timeout", '"timeout"'),
        ("Payroll  TIMEOUT", '"payroll" OR "timeout"'),
        ("why is checkout timing out?", '"why" OR "is" OR "checkout" OR "timing" OR "out"'),
        ("timeout timeout Timeout", '"timeout"'),  # duplicates collapse
        # FTS5 operators become literal terms, not operators
        ("db AND timeout", '"db" OR "and" OR "timeout"'),
        ("NOT sso NEAR(login)", '"not" OR "sso" OR "near" OR "login"'),
        # column filters, prefixes, quotes, grouping and SQL-ish input are stripped
        ("title:payroll", '"title" OR "payroll"'),
        ('time* "broken ^boost (x)', '"time" OR "broken" OR "boost" OR "x"'),
        ("'; DROP TABLE incidents; --", '"drop" OR "table" OR "incidents"'),
        ("snake_case-term", '"snake" OR "case" OR "term"'),
        # unicode letters and digits are kept
        ("café naïve 東京 v2", '"café" OR "naïve" OR "東京" OR "v2"'),
    ],
)
def test_builds_quoted_or_query(q, expected):
    assert build_match_query(q) == expected


@pytest.mark.parametrize("q", ["", "   ", "???", '"*:^()-'])
def test_returns_none_when_no_searchable_terms(q):
    assert build_match_query(q) is None

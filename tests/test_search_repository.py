from datetime import UTC, datetime, timedelta

import pytest
from factories import make_incident

from incident_triage.db import connect, init_db
from incident_triage.models import SearchQuery
from incident_triage.repository import SqliteIncidentRepository

T0 = datetime(2026, 3, 1, tzinfo=UTC)


@pytest.fixture
def repo(tmp_path):
    db = tmp_path / "search.db"
    init_db(db)
    with connect(db) as conn:
        repo = SqliteIncidentRepository(conn)
        repo.upsert_many(
            [
                # 1: term in the title (weight 3)
                make_incident(
                    id=1,
                    title="SSO login timeout",
                    description="Users saw errors.",
                    environment="prod",
                    service="auth",
                    severity="SEV1",
                    tags=["sso", "login"],
                    created_at=T0,
                ),
                # 2: same term only in the description (weight 1)
                make_incident(
                    id=2,
                    title="Slow dashboards",
                    description="A timeout in the report job.",
                    environment="qa",
                    service="reporting",
                    severity="SEV3",
                    tags=["report"],
                    created_at=T0 + timedelta(days=1),
                ),
                # 3: unrelated
                make_incident(
                    id=3,
                    title="Duplicate emails",
                    description="Emails sent twice.",
                    environment="prod",
                    service="notifications",
                    severity="SEV4",
                    tags=["email", "login"],
                    created_at=T0 + timedelta(days=2),
                ),
            ]
        )
        yield repo


def ids(page):
    return [r.id for r in page.results]


def test_title_match_outranks_description_match(repo):
    page = repo.search(SearchQuery(q="timeout"))

    assert ids(page) == [1, 2]
    assert page.total == 2
    assert page.results[0].score > page.results[1].score > 0


def test_snippet_comes_from_description(repo):
    page = repo.search(SearchQuery(q="report"))

    assert page.results[0].snippet == "A timeout in the report job."


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({"environment": "prod"}, [1]),
        ({"service": "reporting"}, [2]),
        ({"severity": "SEV1"}, [1]),
        ({"environment": "qa", "severity": "SEV1"}, []),
    ],
)
def test_filters_narrow_text_search(repo, filters, expected):
    assert ids(repo.search(SearchQuery(q="timeout", **filters))) == expected


def test_tags_must_all_match(repo):
    assert ids(repo.search(SearchQuery(tags=["login"]))) == [3, 1]
    assert ids(repo.search(SearchQuery(tags=["login", "sso"]))) == [1]
    assert ids(repo.search(SearchQuery(tags=["login", "report"]))) == []


def test_filter_only_search_is_newest_first(repo):
    page = repo.search(SearchQuery(environment="prod"))

    assert ids(page) == [3, 1]
    assert all(r.score is None for r in page.results)


def test_total_ignores_pagination(repo):
    page = repo.search(SearchQuery(q="timeout", limit=1, offset=1))

    assert ids(page) == [2]
    assert (page.total, page.limit, page.offset) == (2, 1, 1)


@pytest.mark.parametrize("q", ["???", '"', "title:", "NEAR(", "*", "'; DROP TABLE incidents; --"])
def test_hostile_or_termless_queries_do_not_error(repo, q):
    repo.search(SearchQuery(q=q))  # must not raise an FTS5 syntax error

    assert repo.get(1) is not None  # and the table is intact


def test_equal_scores_are_ordered_newest_first(tmp_path):
    db = tmp_path / "ties.db"
    init_db(db)
    with connect(db) as conn:
        repo = SqliteIncidentRepository(conn)
        same = {"title": "Payroll run delayed", "description": "The run was late."}
        repo.upsert_many(
            [
                make_incident(id=1, created_at=T0, **same),
                make_incident(id=2, created_at=T0 + timedelta(days=5), **same),
                make_incident(id=3, created_at=T0 + timedelta(days=1), **same),
            ]
        )

        assert ids(repo.search(SearchQuery(q="payroll"))) == [2, 3, 1]

import pytest
from fakes import FakeIncidentRepository

from incident_triage.errors import AppError
from incident_triage.models import SearchQuery
from incident_triage.service import IncidentService


@pytest.mark.parametrize("query", [SearchQuery(), SearchQuery(q="   ")])
def test_requires_text_or_a_filter(query):
    with pytest.raises(AppError) as exc:
        IncidentService(FakeIncidentRepository()).search(query)

    assert exc.value.status_code == 400


@pytest.mark.parametrize("query", [SearchQuery(q="timeout"), SearchQuery(tags=["sso"])])
def test_text_or_filter_alone_is_enough(query):
    repo = FakeIncidentRepository()

    IncidentService(repo).search(query)

    assert repo.searched == query

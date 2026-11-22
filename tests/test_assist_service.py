import pytest
from pydantic import ValidationError

from incident_triage.models import AssistRequest


def test_question_is_stripped_and_must_not_be_blank():
    assert AssistRequest(question="  sso login  ").question == "sso login"
    with pytest.raises(ValidationError):
        AssistRequest(question="   ", environment="prod")

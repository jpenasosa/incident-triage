import json
from unittest.mock import MagicMock

import httpx2
import openai
import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from incident_triage.config import Settings
from incident_triage.llm import MAX_OUTPUT_TOKENS, AssistUnavailable, OpenAIClient


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    ids: list[int]


REQUEST = httpx2.Request("POST", "https://api.openai.com/v1/responses")


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{"LLM_API_KEY": "test-key", **overrides})


def parsed_response(
    parsed: BaseModel | None, status: str = "completed", reason: str | None = None
) -> MagicMock:
    response = MagicMock(status=status, output_parsed=parsed)
    response.incomplete_details = MagicMock(reason=reason) if reason else None
    return response


def stub_sdk(result) -> MagicMock:
    """Stands in for openai.OpenAI: `responses.parse` returns or raises `result`."""
    sdk = MagicMock()
    if isinstance(result, Exception):
        sdk.responses.parse.side_effect = result
    else:
        sdk.responses.parse.return_value = result
    return sdk


def call(sdk, **settings_overrides):
    client = OpenAIClient(settings(**settings_overrides), sdk=sdk)
    return client.complete(system="sys", user="usr", output=Answer)


def status_error(cls, status: int):
    return cls("boom", response=httpx2.Response(status, request=REQUEST), body=None)


def validation_error() -> ValidationError:
    try:
        Answer.model_validate_json('{"text": "cut')
    except ValidationError as exc:
        return exc
    raise AssertionError("expected a ValidationError")


# --- configuration ----------------------------------------------------------------------


def test_defaults(monkeypatch):
    for var in ("LLM_MODEL", "LLM_BASE_URL"):
        monkeypatch.delenv(var, raising=False)

    s = Settings(_env_file=None)

    assert (s.LLM_MODEL, s.LLM_BASE_URL) == ("gpt-6-luna", None)


def test_real_sdk_is_configured_from_settings():
    client = OpenAIClient(settings(LLM_TIMEOUT_SECONDS=7))

    assert str(client._sdk.base_url) == "https://api.openai.com/v1/"
    assert client._sdk.timeout == 7
    assert client._sdk.max_retries == 1


def test_base_url_override():
    client = OpenAIClient(settings(LLM_BASE_URL="https://proxy.example/v1/"))

    assert str(client._sdk.base_url) == "https://proxy.example/v1/"


# --- request shape ----------------------------------------------------------------------


def test_uses_responses_parse_with_the_output_model():
    sdk = stub_sdk(parsed_response(Answer(text="hi", ids=[1])))

    result = call(sdk, LLM_MODEL="gpt-6-luna")

    assert result == Answer(text="hi", ids=[1])
    sdk.responses.parse.assert_called_once_with(
        model="gpt-6-luna",
        instructions="sys",
        input="usr",
        text_format=Answer,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        store=False,
    )


# --- failures: all opaque to users, actionable in logs ----------------------------------


def test_missing_key_fails_fast_without_a_client(capsys):
    client = OpenAIClient(Settings(_env_file=None))  # no LLM_API_KEY

    with pytest.raises(AssistUnavailable) as exc:
        client.complete(system="s", user="u", output=Answer)

    assert client._sdk is None
    assert exc.value.message == "Assist service is not accessible"
    assert "LLM_API_KEY is not set" in exc.value.cause
    log = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert (log["event"], log["level"]) == ("llm_call_failed", "error")


@pytest.mark.parametrize(
    ("error", "cause_fragment"),
    [
        (openai.APITimeoutError(request=REQUEST), "timed out after 20.0s"),
        (status_error(openai.AuthenticationError, 401), "rejected LLM_API_KEY"),
        (status_error(openai.PermissionDeniedError, 403), "rejected LLM_API_KEY"),
        (status_error(openai.RateLimitError, 429), "HTTP 429"),
        (status_error(openai.InternalServerError, 500), "HTTP 500"),
        (status_error(openai.BadRequestError, 400), "HTTP 400"),
        (openai.APIConnectionError(request=REQUEST), "could not reach OpenAI"),
        (
            openai.APIResponseValidationError(
                response=httpx2.Response(200, request=REQUEST), body=None
            ),
            "failed SDK validation (HTTP 200)",
        ),
        (openai.OpenAIError("anything else"), "unexpected SDK error: OpenAIError"),
        (validation_error(), "did not match Answer"),
    ],
)
def test_errors_map_to_assist_unavailable(error, cause_fragment):
    with pytest.raises(AssistUnavailable) as exc:
        call(stub_sdk(error))

    assert cause_fragment in exc.value.cause
    assert exc.value.status_code == 503


@pytest.mark.parametrize(
    ("response", "cause_fragment"),
    [
        (
            parsed_response(None, status="incomplete", reason="max_output_tokens"),
            "status 'incomplete' (reason='max_output_tokens')",
        ),
        (parsed_response(None, status="failed"), "status 'failed'"),
        (parsed_response(None), "no parsed output"),  # e.g. a refusal
    ],
)
def test_unusable_responses_map_to_assist_unavailable(response, cause_fragment):
    with pytest.raises(AssistUnavailable) as exc:
        call(stub_sdk(response))

    assert cause_fragment in exc.value.cause


def test_error_body_does_not_leak_the_cause(client, app):
    @app.get("/_test/llm")
    def _llm():
        raise AssistUnavailable("LLM_API_KEY is not set")

    body = client.get("/_test/llm").json()["error"]

    assert body["code"] == "ASSIST_UNAVAILABLE"
    assert body["message"] == "Assist service is not accessible"
    assert "LLM_API_KEY" not in json.dumps(body)

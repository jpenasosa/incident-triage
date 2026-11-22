import json
from unittest.mock import MagicMock

import httpx2
import openai
import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from incident_triage.config import Settings
from incident_triage.llm import MAX_OUTPUT_TOKENS, REASON_MESSAGES, AssistUnavailable, OpenAIClient


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


# --- failures: a fixed, actionable message per reason; the raw cause only in logs -------


def test_missing_key_fails_fast_without_a_client(capsys):
    client = OpenAIClient(Settings(_env_file=None))  # no LLM_API_KEY

    with pytest.raises(AssistUnavailable) as exc:
        client.complete(system="s", user="u", output=Answer)

    assert client._sdk is None
    assert exc.value.reason == "llm_key_missing"
    assert "LLM_API_KEY" in exc.value.message and "uv run start" in exc.value.message
    assert "LLM_API_KEY is not set" in exc.value.cause
    log = json.loads(capsys.readouterr().out.splitlines()[-1])
    assert (log["event"], log["level"]) == ("llm_call_failed", "error")
    assert log["reason"] == "llm_key_missing"


@pytest.mark.parametrize(
    ("error", "reason", "cause_fragment"),
    [
        (openai.APITimeoutError(request=REQUEST), "llm_timeout", "timed out after 20.0s"),
        (status_error(openai.AuthenticationError, 401), "llm_key_rejected", "rejected"),
        (status_error(openai.PermissionDeniedError, 403), "llm_key_rejected", "rejected"),
        (status_error(openai.NotFoundError, 404), "llm_model_unknown", "HTTP 404"),
        (status_error(openai.RateLimitError, 429), "llm_rate_limited", "HTTP 429"),
        (status_error(openai.InternalServerError, 500), "llm_upstream_error", "HTTP 500"),
        (status_error(openai.BadRequestError, 400), "llm_upstream_error", "HTTP 400"),
        (openai.APIConnectionError(request=REQUEST), "llm_upstream_error", "could not reach"),
        (
            openai.APIResponseValidationError(
                response=httpx2.Response(200, request=REQUEST), body=None
            ),
            "llm_upstream_error",
            "failed SDK validation (HTTP 200)",
        ),
        (openai.OpenAIError("anything else"), "llm_upstream_error", "unexpected SDK error"),
        (validation_error(), "llm_upstream_error", "did not match Answer"),
    ],
)
def test_errors_map_to_reasons(error, reason, cause_fragment):
    with pytest.raises(AssistUnavailable) as exc:
        call(stub_sdk(error))

    assert exc.value.reason == reason
    assert exc.value.message == REASON_MESSAGES[reason]
    assert exc.value.details == [{"reason": reason}]
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

    assert exc.value.reason == "llm_upstream_error"
    assert cause_fragment in exc.value.cause


@pytest.mark.parametrize("reason", sorted(REASON_MESSAGES))
def test_error_body_has_the_fix_but_not_the_cause(client, app, reason):
    secret_cause = "provider said: Incorrect API key provided: sk-proj-abc123"

    @app.get("/_test/llm")
    def _llm():
        raise AssistUnavailable(reason, secret_cause)

    body = client.get("/_test/llm").json()["error"]

    assert body["code"] == "ASSIST_UNAVAILABLE"
    assert body["message"] == REASON_MESSAGES[reason]
    assert body["details"] == [{"reason": reason}]
    assert "sk-proj" not in json.dumps(body) and "provider said" not in json.dumps(body)


def test_every_message_says_what_to_do():
    for reason, message in REASON_MESSAGES.items():
        assert any(w in message for w in ("Set ", "Retry", "retry")), reason

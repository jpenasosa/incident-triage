import json
from unittest.mock import MagicMock

import httpx2
import openai
import pytest
from openai import pydantic_responses_function_tool
from pydantic import BaseModel, ConfigDict, ValidationError

from incident_triage.config import Settings
from incident_triage.llm import (
    MAX_OUTPUT_TOKENS,
    REASON_MESSAGES,
    AssistUnavailable,
    OpenAIClient,
    Tool,
)


class Answer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str
    ids: list[int]


class LookupArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    q: str


REQUEST = httpx2.Request("POST", "https://api.openai.com/v1/responses")


def settings(**overrides) -> Settings:
    return Settings(_env_file=None, **{"LLM_API_KEY": "test-key", **overrides})


def parsed_response(
    parsed: BaseModel | None, status: str = "completed", reason: str | None = None
) -> MagicMock:
    response = MagicMock(status=status, output_parsed=parsed, output=[])
    response.incomplete_details = MagicMock(reason=reason) if reason else None
    return response


def item(type_: str, **fields) -> MagicMock:
    """An SDK output item whose to_dict() is `fields` plus the SDK's parsed extras."""
    mock = MagicMock(type=type_, **{k: v for k, v in fields.items() if k != "name"})
    mock.name = fields.get("name")  # `name` is special to MagicMock's constructor
    extras = (
        {"parsed_arguments": fields.get("parsed_arguments")} if type_ == "function_call" else {}
    )
    plain = {k: v for k, v in fields.items() if k != "parsed_arguments"}
    mock.to_dict.return_value = {"type": type_, **plain, **extras}
    return mock


def tool_call_response(q: str, call_id: str = "call_1") -> MagicMock:
    """A turn where the model reasons, then calls `lookup`."""
    response = parsed_response(None)
    response.output = [
        item("reasoning", id="rs_1", encrypted_content="enc", summary=[]),
        item(
            "function_call",
            id="fc_1",
            call_id=call_id,
            name="lookup",
            arguments=json.dumps({"q": q}),
            parsed_arguments=LookupArgs(q=q),
        ),
    ]
    return response


def stub_sdk(*results) -> MagicMock:
    """Stands in for openai.OpenAI: each `responses.parse` call returns or raises the next
    of `results`."""
    sdk = MagicMock()
    sdk.responses.parse.side_effect = list(results)
    return sdk


def lookup_tool(seen: list | None = None) -> Tool:
    def run(args: LookupArgs) -> str:
        if seen is not None:
            seen.append(args)
        return f"results for {args.q}"

    return Tool(name="lookup", description="Look things up.", args=LookupArgs, run=run)


def call(sdk, *, tools=None, max_tool_calls=3, **settings_overrides):
    client = OpenAIClient(settings(**settings_overrides), sdk=sdk)
    return client.run_tools(
        system="sys",
        user="usr",
        tools=tools or [lookup_tool()],
        output=Answer,
        max_tool_calls=max_tool_calls,
    )


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


def test_first_turn_requires_a_tool_call_and_sends_strict_tools():
    sdk = stub_sdk(parsed_response(Answer(text="hi", ids=[1])))

    result = call(sdk, LLM_MODEL="gpt-6-luna")

    assert result == Answer(text="hi", ids=[1])
    sdk.responses.parse.assert_called_once_with(
        model="gpt-6-luna",
        instructions="sys",
        input=[{"role": "user", "content": "usr"}],
        text_format=Answer,
        tools=[
            pydantic_responses_function_tool(
                LookupArgs, name="lookup", description="Look things up."
            )
        ],
        tool_choice="required",
        parallel_tool_calls=False,
        max_output_tokens=MAX_OUTPUT_TOKENS,
        store=False,
    )
    assert sdk.responses.parse.call_args.kwargs["tools"][0]["strict"] is True


# --- tool loop --------------------------------------------------------------------------


def test_tool_calls_run_and_the_transcript_is_replayed_without_parsed_extras():
    seen = []
    sdk = stub_sdk(tool_call_response("sso"), parsed_response(Answer(text="ok", ids=[7])))

    result = call(sdk, tools=[lookup_tool(seen)])

    assert result == Answer(text="ok", ids=[7])
    assert seen == [LookupArgs(q="sso")]
    second = sdk.responses.parse.call_args_list[1].kwargs
    assert second["tool_choice"] == "auto"
    assert second["input"] == [
        {"role": "user", "content": "usr"},
        {"type": "reasoning", "id": "rs_1", "encrypted_content": "enc", "summary": []},
        {
            "type": "function_call",
            "id": "fc_1",
            "call_id": "call_1",
            "name": "lookup",
            "arguments": '{"q": "sso"}',
        },
        {"type": "function_call_output", "call_id": "call_1", "output": "results for sso"},
    ]


def test_the_turn_after_the_cap_must_answer():
    sdk = stub_sdk(
        tool_call_response("a", "c1"),
        tool_call_response("b", "c2"),
        parsed_response(Answer(text="ok", ids=[])),
    )

    call(sdk, max_tool_calls=2)

    choices = [c.kwargs["tool_choice"] for c in sdk.responses.parse.call_args_list]
    assert choices == ["required", "auto", "none"]


def test_a_tool_call_past_the_cap_is_an_upstream_error():
    seen = []
    sdk = stub_sdk(tool_call_response("a", "c1"), tool_call_response("b", "c2"))

    with pytest.raises(AssistUnavailable) as exc:
        call(sdk, tools=[lookup_tool(seen)], max_tool_calls=1)

    assert exc.value.reason == "llm_upstream_error"
    assert "kept calling tools" in exc.value.cause
    assert [a.q for a in seen] == ["a"]  # the call past the cap never ran


def test_an_unknown_tool_is_an_upstream_error():
    response = tool_call_response("a")
    response.output[1].name = "rm_rf"

    with pytest.raises(AssistUnavailable) as exc:
        call(stub_sdk(response))

    assert "unknown tool 'rm_rf'" in exc.value.cause


# --- failures: a fixed, actionable message per reason; the raw cause only in logs -------


def test_missing_key_fails_fast_without_a_client(capsys):
    client = OpenAIClient(Settings(_env_file=None))  # no LLM_API_KEY

    with pytest.raises(AssistUnavailable) as exc:
        client.run_tools(
            system="s", user="u", tools=[lookup_tool()], output=Answer, max_tool_calls=1
        )

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

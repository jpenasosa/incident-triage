"""LLM client over the OpenAI Responses API (ADR 0004), with a function-calling loop
for tools the caller supplies (ADR 0007).

Every failure surfaces as AssistUnavailable: API users get a fixed, actionable message
for its reason (what to set, or to retry), never provider error text or secrets. The raw
cause is logged at ERROR with the request's correlation ID.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any, Literal, NoReturn, Protocol, TypeVar

import openai
import structlog
from openai import OpenAI, pydantic_responses_function_tool
from pydantic import BaseModel, ValidationError

from incident_triage.config import Settings
from incident_triage.errors import AppError

# Reasoning tokens count toward this on reasoning models.
MAX_OUTPUT_TOKENS = 16_000
# The SDK retries timeouts too, so wall-clock time is up to timeout × (retries + 1).
MAX_RETRIES = 1

log = structlog.get_logger()

T = TypeVar("T", bound=BaseModel)


Reason = Literal[
    "llm_key_missing",
    "llm_key_rejected",
    "llm_model_unknown",
    "llm_timeout",
    "llm_rate_limited",
    "llm_upstream_error",
]

_RESTART = "restart the server with `uv run start`"
REASON_MESSAGES: dict[Reason, str] = {
    "llm_key_missing": f"Assist needs an OpenAI API key. Set LLM_API_KEY in .env and {_RESTART}.",
    "llm_key_rejected": (
        f"OpenAI rejected the API key. Set a valid LLM_API_KEY in .env and {_RESTART}."
    ),
    "llm_model_unknown": (
        "OpenAI did not find the configured model. Set LLM_MODEL in .env to a model your key "
        f"can use (and check LLM_BASE_URL if you set it), then {_RESTART}."
    ),
    "llm_timeout": (
        "The LLM call timed out. Retry, or raise LLM_TIMEOUT_SECONDS in .env (maximum 120) "
        f"and {_RESTART}."
    ),
    "llm_rate_limited": (
        "OpenAI rate-limited the request. Retry later, and check the OpenAI account's usage "
        "limits and billing."
    ),
    "llm_upstream_error": (
        "The LLM call failed. Retry later. If it keeps failing, check LLM_BASE_URL (if you set "
        "it) and the server log line `llm_call_failed` with this correlation_id."
    ),
}


class AssistUnavailable(AppError):
    status_code = 503
    code = "ASSIST_UNAVAILABLE"

    def __init__(self, reason: Reason, cause: str):
        super().__init__(REASON_MESSAGES[reason], details=[{"reason": reason}])
        self.reason = reason
        self.cause = cause  # for logs only; may hold provider text, never returned to users


@dataclass(frozen=True)
class Tool:
    """A function the model may call. `run` gets the validated `args` model and returns
    the text handed back to the model."""

    name: str
    description: str
    args: type[BaseModel]
    run: Callable[[Any], str]


class LLMClient(Protocol):
    def ensure_available(self) -> None:
        """Raise AssistUnavailable now if no call could succeed (e.g. no API key)."""
        ...

    def run_tools(
        self,
        *,
        system: str,
        user: str,
        tools: Sequence[Tool],
        output: type[T],
        max_tool_calls: int,
    ) -> T:
        """Let the model call `tools` (at least once, at most `max_tool_calls` times),
        then return its final reply parsed as `output`, or raise AssistUnavailable."""
        ...


class OpenAIClient:
    """Structured output via `responses.parse`: the SDK derives a strict JSON schema
    from `output` and validates the reply against it. Output models should forbid extra
    fields so the schema and the parser agree.
    """

    def __init__(self, settings: Settings, sdk: OpenAI | None = None):
        self._model = settings.LLM_MODEL
        self._timeout = settings.LLM_TIMEOUT_SECONDS
        self._sdk = sdk
        if sdk is None and settings.LLM_API_KEY is not None:
            self._sdk = OpenAI(
                api_key=settings.LLM_API_KEY.get_secret_value(),
                base_url=settings.LLM_BASE_URL,  # None = api.openai.com
                timeout=self._timeout,
                max_retries=MAX_RETRIES,
            )

    def ensure_available(self) -> None:
        if self._sdk is None:
            self._fail("llm_key_missing", "LLM_API_KEY is not set")

    def run_tools(
        self,
        *,
        system: str,
        user: str,
        tools: Sequence[Tool],
        output: type[T],
        max_tool_calls: int,
    ) -> T:
        """One tool call per turn: the first turn must call a tool, later turns may, and
        the turn after `max_tool_calls` calls must answer. With store=False nothing is
        kept server-side, so every turn resends the full transcript."""
        self.ensure_available()  # fail fast: no network call without a key

        by_name = {t.name: t for t in tools}
        tool_params = [
            pydantic_responses_function_tool(t.args, name=t.name, description=t.description)
            for t in tools
        ]
        transcript: list[dict[str, Any]] = [{"role": "user", "content": user}]
        for turn in range(max_tool_calls + 1):
            choice = "required" if turn == 0 else "none" if turn == max_tool_calls else "auto"
            response = self._parse(
                instructions=system,
                input=transcript,
                text_format=output,
                tools=tool_params,
                tool_choice=choice,
                parallel_tool_calls=False,
            )
            calls = [item for item in response.output if item.type == "function_call"]
            if not calls:
                if response.output_parsed is None:  # e.g. the model refused
                    self._fail("llm_upstream_error", "response contained no parsed output")
                return response.output_parsed
            if turn == max_tool_calls:
                break
            transcript += [_replayable(item) for item in response.output]
            for call in calls:
                tool = by_name.get(call.name)
                if tool is None:
                    self._fail("llm_upstream_error", f"model called unknown tool {call.name!r}")
                transcript.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": tool.run(call.parsed_arguments),
                    }
                )
        self._fail("llm_upstream_error", f"model kept calling tools after {max_tool_calls} calls")

    def _parse(self, *, text_format: type[BaseModel], **params: Any) -> Any:
        """One `responses.parse` request, with every SDK failure mapped to a reason."""
        try:
            response = self._sdk.responses.parse(
                model=self._model,
                text_format=text_format,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                store=False,  # don't retain Incident data on the provider side
                **params,
            )
        except openai.APITimeoutError:  # subclass of APIConnectionError: must come first
            self._fail("llm_timeout", f"timed out after {self._timeout}s (LLM_TIMEOUT_SECONDS)")
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            self._fail(
                "llm_key_rejected",
                f"OpenAI rejected LLM_API_KEY (HTTP {exc.status_code})",
                request_id=exc.request_id,
            )
        except openai.NotFoundError as exc:  # unknown model (or a wrong LLM_BASE_URL)
            self._fail("llm_model_unknown", "OpenAI returned HTTP 404", request_id=exc.request_id)
        except openai.RateLimitError as exc:
            self._fail("llm_rate_limited", "OpenAI returned HTTP 429", request_id=exc.request_id)
        except openai.APIStatusError as exc:
            self._fail(
                "llm_upstream_error",
                f"OpenAI returned HTTP {exc.status_code}",
                request_id=exc.request_id,
            )
        except openai.APIConnectionError:
            self._fail("llm_upstream_error", "could not reach OpenAI")
        except openai.APIResponseValidationError as exc:  # sibling of APIStatusError
            self._fail(
                "llm_upstream_error",
                f"OpenAI response failed SDK validation (HTTP {exc.status_code})",
                request_id=exc.response.headers.get("x-request-id"),
            )
        except openai.OpenAIError as exc:  # any other SDK error must still map to a 503
            self._fail("llm_upstream_error", f"unexpected SDK error: {type(exc).__name__}")
        except ValidationError as exc:  # parse() validates replies and tool arguments
            self._fail(
                "llm_upstream_error",
                f"response did not match {exc.title} ({exc.error_count()} errors); "
                "it may have been truncated",
            )

        if response.status != "completed":
            reason = response.incomplete_details.reason if response.incomplete_details else None
            self._fail(
                "llm_upstream_error", f"response status {response.status!r} (reason={reason!r})"
            )
        return response

    def _fail(self, reason: Reason, cause: str, **context: object) -> NoReturn:
        log.error(
            "llm_call_failed",
            reason=reason,
            cause=cause,
            model=self._model,
            **{k: v for k, v in context.items() if v is not None},
        )
        raise AssistUnavailable(reason, cause)


def _replayable(item: Any) -> dict[str, Any]:
    """An output item as input for the next turn: reasoning items keep their
    encrypted_content and messages their phase, minus the SDK's parsed extras
    (mirrors the SDK's own tool runner)."""
    data = item.to_dict()
    data.pop("created_by", None)
    if item.type == "function_call":
        data.pop("parsed_arguments", None)
        if data.get("id") is None:
            data.pop("id", None)
    if item.type == "message":
        for part in data.get("content", []):
            if part.get("type") == "output_text":
                part.pop("parsed", None)
    return data

"""LLM client over the OpenAI Responses API (ADR 0004).

Every failure, including a missing key, surfaces as one opaque AssistUnavailable for
API users; the actionable cause is logged at ERROR with the request's correlation ID.
"""

from typing import NoReturn, Protocol, TypeVar

import openai
import structlog
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from incident_triage.config import Settings
from incident_triage.errors import AppError

# Reasoning tokens count toward this on reasoning models.
MAX_OUTPUT_TOKENS = 16_000
# The SDK retries timeouts too, so wall-clock time is up to timeout × (retries + 1).
MAX_RETRIES = 1

log = structlog.get_logger()

T = TypeVar("T", bound=BaseModel)


class AssistUnavailable(AppError):
    status_code = 503
    code = "ASSIST_UNAVAILABLE"

    def __init__(self, cause: str):
        super().__init__("Assist service is not accessible")
        self.cause = cause  # for logs only; never returned to API users


class LLMClient(Protocol):
    def complete(self, *, system: str, user: str, output: type[T]) -> T:
        """Return the model's reply parsed as `output`, or raise AssistUnavailable."""
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

    def complete(self, *, system: str, user: str, output: type[T]) -> T:
        if self._sdk is None:  # fail fast: no network call without a key
            self._fail("LLM_API_KEY is not set; set it in .env or the environment and restart")

        try:
            response = self._sdk.responses.parse(
                model=self._model,
                instructions=system,
                input=user,
                text_format=output,
                max_output_tokens=MAX_OUTPUT_TOKENS,
                store=False,  # don't retain Incident data on the provider side
            )
        except openai.APITimeoutError:  # subclass of APIConnectionError: must come first
            self._fail(f"timed out after {self._timeout}s (LLM_TIMEOUT_SECONDS)")
        except (openai.AuthenticationError, openai.PermissionDeniedError) as exc:
            self._fail(
                f"OpenAI rejected LLM_API_KEY (HTTP {exc.status_code}); "
                "set a valid key and restart",
                request_id=exc.request_id,
            )
        except openai.APIStatusError as exc:
            self._fail(f"OpenAI returned HTTP {exc.status_code}", request_id=exc.request_id)
        except openai.APIConnectionError:
            self._fail("could not reach OpenAI")
        except openai.APIResponseValidationError as exc:  # sibling of APIStatusError
            self._fail(
                f"OpenAI response failed SDK validation (HTTP {exc.status_code})",
                request_id=exc.response.headers.get("x-request-id"),
            )
        except openai.OpenAIError as exc:  # any other SDK error must still map to a 503
            self._fail(f"unexpected SDK error: {type(exc).__name__}")
        except ValidationError as exc:  # parse() validates the reply against `output`
            self._fail(
                f"response did not match {output.__name__} ({exc.error_count()} errors); "
                "it may have been truncated"
            )

        if response.status != "completed":
            reason = response.incomplete_details.reason if response.incomplete_details else None
            self._fail(f"response status {response.status!r} (reason={reason!r})")
        if response.output_parsed is None:  # e.g. the model refused
            self._fail("response contained no parsed output")
        return response.output_parsed

    def _fail(self, cause: str, **context: object) -> NoReturn:
        log.error(
            "llm_call_failed",
            cause=cause,
            model=self._model,
            **{k: v for k, v in context.items() if v is not None},
        )
        raise AssistUnavailable(cause)

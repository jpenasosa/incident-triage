# OpenAI Responses API as the only LLM provider

Supersedes [ADR 0002](0002-openai-compatible-llm-client.md). Problems with the Anthropic account removed the need to support several providers. Assist now calls OpenAI directly through the Responses API (`client.responses.parse`). `LLM_PROVIDER` is gone, and `LLM_BASE_URL` remains only as an optional proxy or Azure OpenAI override.

## Considered Options

- **Keep the OpenAI-compatible Chat Completions layer for Anthropic, Google and OpenAI (ADR 0002):** rejected. It held us to the lowest common denominator: no `store=False`, no SDK-derived strict schema, and each provider's compatibility quirks. That flexibility was only worth having while we were experimenting across providers.
- **Chat Completions on OpenAI:** rejected. We had used it only for compatibility with Anthropic and Google Gemini. The Responses API is intended to supersede it, supports function calling and structured responses, and `responses.parse(text_format=Model)` builds and validates the strict JSON schema from the Pydantic model in one call.

## Consequences

- Switching providers again means a new `LLMClient` implementation. Assist depends only on the `LLMClient` protocol, so nothing else changes.
- Requests send `store=False`, so Incident data isn't retained by OpenAI for later retrieval.

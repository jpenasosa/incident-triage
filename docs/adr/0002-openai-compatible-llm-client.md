---
status: superseded by ADR 0004
---

# OpenAI-compatible LLM client for all providers

We call every LLM provider, Anthropic included, through the `openai` SDK. `LLM_PROVIDER` maps to a `base_url`, and an optional `LLM_BASE_URL` overrides it. This keeps one small client and makes it cheap to try models across providers while prototyping.

## Consequences

- Anthropic describes its compatibility layer as "not considered a long-term or production-ready solution". We give up native features such as prompt caching and citations.
- Only `response_format` `json_schema` with `strict: true` is portable.
- `temperature` is never sent, because recent Claude models reject values below 1.
- If this were productized, a gateway such as OpenRouter or Vercel AI Gateway would provide OpenAI compatibility instead.

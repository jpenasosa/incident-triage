# Tradeoffs

_This is a running log, updated with each ticket and finalised in ticket 08._

## Done

- Ticket 01, scaffolding:
  - config from env vars, failing at startup with a clear message
  - JSON logs with a correlation ID on every request log line
  - one consistent error shape

## Not done

_(filled in as tickets land)_

## Deliberate interpretations of the brief

- **"3 to 5 relevant incidents":** assist returns **1–5**. Making up relevance to reach three would break the grounding rules.
- **"Return a clear error explaining what to set":** we read this as covering the *operator*:
  - A missing `LLM_API_KEY` stops the app at startup with the exact fix.
  - Runtime LLM failures return an opaque `ASSIST_UNAVAILABLE` to API users, with full detail logged at ERROR under the correlation ID.
- **Customer Drafts are generic on purpose:** they're built from customer-safe fields only (ADR 0003).

## Known issues and risks

- Uvicorn's own startup and shutdown lines are plain text with no correlation ID. Every *request* log line has one.
- The OpenAI-compatible layer for Anthropic isn't production-grade, according to Anthropic (ADR 0002).

## With 2 more hours

_(to do)_

## With 2 more days

_(to do)_

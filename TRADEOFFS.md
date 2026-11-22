# Tradeoffs

_This is a running log, updated with each ticket and finalised in ticket 08._

## Done

- Ticket 01, scaffolding:
  - config from env vars, with a clear startup error for invalid values; `LLM_API_KEY` is optional
  - JSON logs with a correlation ID on every request log line
  - one consistent error shape
- Ticket 02, ingest:
  - a deterministic generator producing 300 Incidents
  - `POST /ingest` takes NDJSON and is atomic and idempotent, matching records on `id` and using a content hash for updated versus skipped
  - SQLite with FTS5, kept in sync on update
- Ticket 03: `GET /incidents/{id}` returns the full record, a 404 in the standard shape for an unknown id, and a 422 for a malformed id
- Ticket 04: `GET /search`. Ranked FTS5 search (BM25 with column weights) plus filters for environment, service, severity and tags (all tags must match), with pagination and a `total` count. User text is turned into quoted OR terms, so FTS5 syntax can't be injected.
- Ticket 05: the LLM client (`llm.py`). It calls the OpenAI Responses API (`responses.parse`), which derives a strict JSON schema from the Pydantic output model and validates the reply against it. Requests send `store=False`. A live call has been confirmed (about 2s). The timeout and retries are bounded. Every failure becomes `ASSIST_UNAVAILABLE`, with the cause logged at ERROR (opaque at first, made actionable in ticket 10). It isn't wired into an endpoint yet; that's ticket 06.
- Ticket 06:
  - `POST /assist` retrieves Candidates through `search()`, deduplicates them by title, and runs an internal LLM analysis over the full records.
  - A deterministic guardrail drops any citation of an Incident the model wasn't shown, or whose evidence isn't found word for word in the stored record. It falls back to a fixed "need more info" response when the model reports insufficient context or no valid citation remains, and zero Candidates means no LLM call at all.
  - A second LLM call wrote the Customer Draft from allowlisted, customer-safe fields only (ADR 0003), with a backstop for identifiers and length. Ticket 11 replaced both.
  - Every step is logged, and the full pipeline has been confirmed against the real API (about 4.6s for both calls).
- Ticket 10: assist failures are actionable in the API response. Each failure maps to a `reason` with a fixed message saying what to set or do (`llm_key_missing`, `llm_key_rejected`, `llm_model_unknown`, `llm_timeout`, `llm_rate_limited`, `llm_upstream_error`). Provider error text and secrets never reach the response, and the raw cause is logged at ERROR. The missing-key, rejected-key and unknown-model cases were confirmed against the real API.
- Ticket 11: the Customer Draft is a prepared template chosen by the model's `customer_reply` (ADR 0006, superseding ADR 0003). No model writes customer-facing text, and assist makes one LLM call (2.3–4.7s live). Live runs picked `investigating` for a plain report and `fix_in_progress` when the question said a fix was underway.
- **Integer Incident ids:** the id is a caller-supplied positive integer stored as SQLite's `INTEGER PRIMARY KEY`, the rowid itself. That avoids the hidden unique index a `TEXT` key creates, and FTS rows share the id directly. The cost is that ids aren't recognisable in free text the way `INC-42` would be. Since ticket 11 no model writes customer text, so ids can't reach the draft at all.

## Not done

- **Ingest by multipart file upload:** the body is raw NDJSON instead. It's simpler, and curl's `--data-binary @file` covers the use case.
- **Deleting Incidents through ingest:** Incidents missing from a later batch are kept, not removed.

## Deliberate interpretations of the brief

- **"3 to 5 relevant incidents":** assist returns **1–5**. Making up relevance to reach three would break the grounding rules.
- **"Return a clear error explaining what to set and how to run it":**
  - A missing `LLM_API_KEY` doesn't block startup, so search and ingest work without one. The app logs a WARNING at startup saying what to set.
  - `/assist` without a key fails fast, with no search and no LLM call.
  - Each LLM failure returns `ASSIST_UNAVAILABLE` with a fixed message for its category: which variable to set and to restart with `uv run start`, or to retry. A `details` reason lets clients branch on the category.
  - The messages are fixed text, so provider error bodies (which can echo part of a key) never reach API users. The raw cause is logged at ERROR under the correlation ID. This was opaque until ticket 10. The trade-off is that API users now learn configuration facts, such as "the key was rejected". That's acceptable for an internal triage tool, but a public API would keep these messages for operators only.
- **Customer Drafts are generic on purpose:** four prepared templates (ADR 0006). That's fine for a prototype, and a production system would use more granular templates.

## Known issues and risks

- Uvicorn's own startup and shutdown lines are plain text with no correlation ID. Every *request* log line has one.
- **Single provider.** Only OpenAI is supported (ADR 0004, superseding ADR 0002). Another provider means a new `LLMClient` implementation.
- **The synthetic text repeats.** There are 24 failure templates, and only numbers and weekdays vary. That's realistic enough for search and assist demos, but many Incidents look alike.
- **The hash covers the whole record.** A change to any field, including `created_at`, counts as `updated`. There's no notion of which fields matter.
- **The schema is applied only when the database file is new.** Adding a schema file later has no effect on an existing database: delete `data/incidents.db` and ingest again. If the first initialisation fails part-way, the file is left behind and later starts skip it, so delete the file in that case too.
- **No stemming.** The default `unicode61` tokenizer matches whole words only: "timing" doesn't match "timeout", and "failing" doesn't match "failed". Switching the FTS table to `tokenize='porter unicode61'` would fix that, at the cost of a schema change and re-ingest.
- **OR semantics favour recall.** Every word in `q` widens the result set, and BM25 ranks Incidents matching more words higher. `total` can therefore be large for long questions.
- **No stop-word filtering.** Common words like "the" and "is" match almost every Incident, so a vague question still returns hits (264 in one test). For assist, the LLM then reports insufficient context, which costs one LLM call that a stop-word list could have avoided.
- **The fix replies rest on the model's reading of the question.** `fix_in_progress` and `fix_delivered` are allowed only when the question says so, but code can't verify that choice. The default is `investigating`, so a wrong pick overstates progress at most, and never adds incident details.
- **Assist answers are literal by design (ADR 0005).** No unverified model prose reaches the internal response. Reasons are excerpts verified against the records, and next steps quote stored resolutions plus fixed process steps. That guarantees nothing is invented, but answers don't synthesise across Incidents. A citation the model paraphrases instead of quoting is dropped, which costs recall rather than safety.
- **Duplicate Candidates are collapsed by exact title (case-insensitive).** Near-duplicates with different titles still reach the model separately.
- **Pagination uses offsets.** That's fine for slowly changing data. Under heavy concurrent writes, pages could shift between requests.
- **Timeout budget.** `LLM_TIMEOUT_SECONDS` defaults to 20s with one retry, so the worst case is about 40s per LLM call. Assist makes one call (ADR 0006). A live call took about 2s, but reasoning-heavy prompts can take much longer.
- **Output models shape the strict schema.** `responses.parse` converts the Pydantic output model to a strict JSON schema. Output models should forbid extra fields, so the schema sent and the parser that validates the reply agree.
- **Ingest runs in one transaction per request.** At 1,000 lines that's fast, about 30 ms for 300 lines. Much larger batches would need chunking.

## With 2 more hours

_(to do)_

## With 2 more days

_(to do)_

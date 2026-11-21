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
- **Integer Incident ids:** the id is a caller-supplied positive integer stored as SQLite's `INTEGER PRIMARY KEY`, the rowid itself. That avoids the hidden unique index a `TEXT` key creates, and FTS rows share the id directly. The cost is that ids aren't recognisable in free text the way `INC-42` would be. So the Customer Draft backstop doesn't check for Incident IDs, because ordinary numbers like "42 employees" would trip it. ADR 0003's context isolation already keeps ids out of the draft.

## Not done

- **Ingest by multipart file upload:** the body is raw NDJSON instead. It's simpler, and curl's `--data-binary @file` covers the use case.
- **Deleting Incidents through ingest:** Incidents missing from a later batch are kept, not removed.

## Deliberate interpretations of the brief

- **"3 to 5 relevant incidents":** assist returns **1–5**. Making up relevance to reach three would break the grounding rules.
- **"Return a clear error explaining what to set":** we read this as covering the *operator*:
  - A missing `LLM_API_KEY` doesn't block startup, so search and ingest work without one. The app logs a WARNING at startup saying what to set.
  - `/assist` without a key fails fast, with no search and no LLM call.
  - Any LLM failure, including a missing key, returns an opaque `ASSIST_UNAVAILABLE` to API users, with full detail logged at ERROR under the correlation ID.
- **Customer Drafts are generic on purpose:** they're built from customer-safe fields only (ADR 0003).

## Known issues and risks

- Uvicorn's own startup and shutdown lines are plain text with no correlation ID. Every *request* log line has one.
- The OpenAI-compatible layer for Anthropic isn't production-grade, according to Anthropic (ADR 0002).
- **The synthetic text repeats.** There are 24 failure templates, and only numbers and weekdays vary. That's realistic enough for search and assist demos, but many Incidents look alike.
- **The hash covers the whole record.** A change to any field, including `created_at`, counts as `updated`. There's no notion of which fields matter.
- **The schema is applied only when the database file is new.** Adding a schema file later has no effect on an existing database: delete `data/incidents.db` and ingest again. If the first initialisation fails part-way, the file is left behind and later starts skip it, so delete the file in that case too.
- **No stemming.** The default `unicode61` tokenizer matches whole words only: "timing" doesn't match "timeout", and "failing" doesn't match "failed". Switching the FTS table to `tokenize='porter unicode61'` would fix that, at the cost of a schema change and re-ingest.
- **OR semantics favour recall.** Every word in `q` widens the result set, and BM25 ranks Incidents matching more words higher. `total` can therefore be large for long questions.
- **Pagination uses offsets.** That's fine for slowly changing data. Under heavy concurrent writes, pages could shift between requests.
- **Ingest runs in one transaction per request.** At 1,000 lines that's fast, about 30 ms for 300 lines. Much larger batches would need chunking.

## With 2 more hours

_(to do)_

## With 2 more days

_(to do)_

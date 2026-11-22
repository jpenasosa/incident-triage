# Tradeoffs

## Done

Every required endpoint, the observability contract, tests and docs are in place. Each part was built as its own ticket and PR, reviewed by me and by Aikido.

- **Configuration:**
  - Settings come from env vars or `.env` (`example.env` is the template), and an invalid value gives a clear error at startup.
  - `LLM_API_KEY` is optional: ingest and search work without it, startup logs a WARNING, and `/assist` fails fast.
- **Observability:**
  - JSON logs to stdout, with a correlation ID on every request log line. The ID comes from `X-Correlation-ID` if it's valid, otherwise it's generated, and it's echoed back.
  - `/assist` logs each step: `llm:analyze`, then `tool:search` input and output for every search, `guardrail` (shown, selected and dropped ids) and `draft`.
- **Errors:** one error shape, `{"error": {code, message, correlation_id, details?}}`, for every failure, including 404, 413, 415, 422 and unhandled 500s.
- **Data:**
  - A deterministic generator writes 300 synthetic Incidents. The same seed and pinned Faker version give byte-identical output.
  - Storage is SQLite with an FTS5 index (ADR 0001).
  - Incident ids are positive integers, stored as SQLite's `INTEGER PRIMARY KEY`, the rowid itself.
- **`POST /ingest`:**
  - The body is NDJSON, capped at 5 MB and 1,000 lines.
  - A batch is all or nothing, with errors reported per line.
  - It's idempotent: records match on `id`, and a content hash decides between updated and skipped.
- **`GET /search`:**
  - Ranked BM25 search, weighted towards titles, plus filters for environment, service, severity and tags (every tag must match).
  - Pagination and a `total` count.
  - User text becomes quoted OR terms, so FTS5 syntax can't be injected.
- **`GET /incidents/{id}`:** returns the full record, 404 in the standard shape for an unknown id, and 422 for a malformed id.
- **`POST /assist`:**
  - **Retrieval (ADR 0007):** the model drives retrieval through a strict `search_incidents` function tool backed by `IncidentService.search()`, the same path as `GET /search`. It must search once and may search up to 3 times. Filters set in the request override the model's.
  - **Grounding (ADR 0005):**
    - Each `reason` is an excerpt from the record, checked word for word.
    - Citations are checked against everything any search returned.
    - `next_steps` quote the stored resolutions, plus fixed process steps.
    - The Customer Draft is a prepared template chosen by the model's `customer_reply` (ADR 0006).
    - No unverified model prose reaches the response.
  - **Not enough to go on:** when no search found anything, the model says nothing is relevant, or no citation survives the check, the response says so and asks for more information.
  - **Failures:** each LLM failure returns 503 with a fixed message for its category, saying what to set or do, and a `details` reason.
- **Tests:** 158 tests: query building, the search repository, ingest, the guardrails, the tool loop (stubbed SDK) and the assist flow (scripted fake model), plus API tests. Ruff checks lint and formatting.
- **Docs:**
  - README with run steps, curl examples and a 10-question QA table for assist.
  - `GLOSSARY.md`, ADRs 0001–0007, `PROMPTS.md` and `AI-NOTES.md`.

## Not done

- **`GET /debug/trace/{correlation_id}` (optional in the brief):** skipped for time. The approach would be:
  - An in-memory, bounded ring buffer, for example the last 500 correlation IDs, holding each request's assist steps. They'd be the same data that's already logged.
  - A small `StepRecorder`, or a structlog processor, writes each step both to the log and to the buffer.
  - The endpoint returns the steps or a 404, and is only enabled when `DEBUG_TRACE=true`.
  - Known limits: it's per process, lost on restart, and has no auth. In production the same data would come from a log or trace backend, for example OpenTelemetry spans per assist step.
  - Until then, `grep '"correlation_id": "<id>"'` on the JSON logs gives the same step list.
- **Ingest by multipart file upload:** the body is raw NDJSON instead. It's simpler, and curl's `--data-binary @file` covers the use case.
- **Deleting Incidents through ingest:** Incidents missing from a later batch are kept, not removed.
- **Auth, rate limiting, a container image and CI:** none of them were in scope for a local prototype.

## Deliberate interpretations of the brief

- **"The 3 to 5 most relevant incident IDs":** assist returns **1–5**, a deliberate deviation that I kept after an external review. Padding the list to three when only one or two Incidents are relevant would mean citing irrelevant Incidents, which breaks the grounding rules ("must not invent incident details"). The prompt asks for the most relevant 1–5, and the guardrail drops any citation it can't verify. When nothing qualifies, the response is the "not enough to go on" path.
- **"Logs it as a tool step (`tool:search`)":** read as function calling. The model chooses what to search for, and each call is logged as a `tool:search` step (ADR 0007). It used to be a fixed search with the question, which met the letter of the brief but not its intent.
- **"Return a clear error explaining what to set and how to run it":**
  - Every LLM failure returns `ASSIST_UNAVAILABLE` with a fixed message for its category: which variable to set and to restart with `uv run start`, or to retry. A `details` reason lets clients branch on the category.
  - The messages are fixed text, so provider error bodies, which can echo part of a key, never reach API users. The raw cause is logged at ERROR under the correlation ID.
  - The trade-off is that API users learn configuration facts, such as "the key was rejected". That's acceptable for an internal triage tool, but a public API would show them to operators only.
- **"A short customer facing draft":** five prepared templates (ADR 0006): `request_details`, `acknowledged`, `investigating`, `fix_in_progress` and `fix_delivered`. A draft never describes an Incident, so it can't invent details. That's fine for a prototype, and a production system would use more granular templates.
- **Environment variables:** the brief's example lists `LLM_PROVIDER` and `DATA_PATH`. Neither is used:
  - Only OpenAI is supported (ADR 0004).
  - Data is sent through `/ingest` rather than read from a path at startup.
  - `DB_PATH` is where the SQLite file lives.

## Known issues and risks

- **Retrieval depends on the model's queries (ADR 0007).** The model can search badly, for example with too narrow a query or an unhelpful filter. Up to 3 searches and "try other words" in the prompt soften that, and the engineer's filters are always enforced. It costs latency and tokens: one extra LLM round-trip per search, and every record returned goes back into the model's context.
- **Results vary between runs.** Which Incident is cited, how many searches run, and whether a vague question cites something or takes the "not enough to go on" path can all change. The chosen reply matched every row of the README QA table in two live runs.
- **Status replies rest on the model's reading of the question.** `investigating`, `fix_in_progress` and `fix_delivered` are allowed only when the question says so, but code can't verify that choice. The default, `acknowledged`, claims no status, so a wrong pick can at worst overstate progress, and never adds incident details.
- **Assist answers are literal by design (ADR 0005).** That guarantees nothing is invented, but answers don't synthesise across Incidents. A citation the model paraphrases instead of quoting is dropped, which costs recall rather than safety.
- **Timeout budget.** `LLM_TIMEOUT_SECONDS` defaults to 20s with one retry, so the worst case is about 40s per LLM call. Assist makes 2–4 calls, so its worst case is about 160s, although live assists took 3.7–9.6s. There's no overall deadline per request.
- **Single provider.** Only OpenAI is supported (ADR 0004, superseding ADR 0002). Another provider means a new `LLMClient` implementation.
- **No stemming.** The default `unicode61` tokenizer matches whole words only: "timing" doesn't match "timeout", and "failing" doesn't match "failed". The model's ability to rephrase hides some of this in assist, but not in `GET /search`.
- **No stop-word filtering, and OR semantics favour recall.** Common words like "the" and "is" match almost every Incident, so `total` can be large for long or vague questions (264 hits in one test). For assist, that's noise in the model's context and sometimes an extra search.
- **The synthetic text repeats.** There are 24 failure templates, and only numbers and weekdays vary. That's realistic enough for demos, but many Incidents look alike, and ties are broken newest first.
- **Duplicate Candidates are collapsed by exact title (case-insensitive).** Near-duplicates with different titles still reach the model separately.
- **The hash covers the whole record.** A change to any field, including `created_at`, counts as `updated`.
- **The schema is applied only when the database file is new.** After a schema change, delete `data/incidents.db` and ingest again. If the first initialisation fails part-way, delete the file in that case too.
- **Pagination uses offsets.** That's fine for slowly changing data. Under heavy concurrent writes, pages could shift between requests.
- **Ingest runs in one transaction per request.** At the 1,000-line cap that's fast, about 30 ms for 300 lines. Much larger batches would need chunking.
- **Output models shape the strict schema.** `responses.parse` converts Pydantic models into strict JSON schemas, so output and tool-argument models must forbid extra fields and make every field required (nullable instead of optional).
- **Uvicorn's own startup and shutdown lines** are plain text with no correlation ID. Every *request* log line has one.

## With 2 more hours

1. **Turn the README QA table into an opt-in live test:** `pytest -m live`, skipped without a key. It would assert each row's expected reply and that the engineer's filters stay on every search, so prompt changes get checked the same way every time.
2. **Add an overall deadline for assist,** for example 30s across all turns, mapped to the existing `llm_timeout` reason. That caps the 160s worst case.
3. **Build `/debug/trace/{correlation_id}`** with the approach above (behind `DEBUG_TRACE=true`). The step data is already structured, so it's mostly a recorder and a route.
4. **Add stemming and stop words:** `tokenize='porter unicode61'` plus a small stop-word list in `build_match_query`, measured with the QA test before and after. It's a schema change, so the database needs rebuilding.

## With 2 more days

- **Better retrieval:** hybrid search (BM25 plus embeddings) for symptoms worded differently from the records, and near-duplicate collapsing on more than exact titles. Probably on Postgres, with `tsvector` and `pgvector`, once more than one instance needs to share the data.
- **An evaluation set:** a few dozen real-looking questions with the Incidents they should cite, tracking citation precision and recall, reply accuracy, searches per assist and latency per model or prompt change.
- **Production observability:** OpenTelemetry spans for each assist step and LLM call, with token usage and cost per request, replacing the in-memory trace endpoint.
- **Hardening:** auth, rate limits on `/assist` (it's the expensive endpoint), a container image, CI running tests and lint, and an async LLM client so slow calls don't tie up worker threads.
- **Product:**
  - More granular Customer Draft templates, for example per product area or with a workaround.
  - An operator view showing the draft beside the Cited Incidents.
  - Incident deletion or tombstones in ingest.

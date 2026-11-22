# Prompts

These are the prompts used with AI tools during the build, recorded as they were used.

---

**Prompt #1**

* Tool used: Claude Code (Claude Opus 5.5), with the `grill-with-docs` skill
* Goal: Turn the brief into a shared design before writing any code. Settle each decision, record the domain vocabulary, and record the hard-to-reverse choices.
* Prompt (copy/paste):

  ```
  /mattpocock-skills:grill-with-docs incident-triage-take-home.md
  ```
* Result summary: About 38 questions over 6 rounds, each with a recommended answer. It produced `GLOSSARY.md`, ADRs 0001–0003 and a spec. It used sub-agents to research Turso and Anthropic's OpenAI compatibility, and smoke-tested `pyturso` in a scratch venv.
* What you kept: Most recommendations (FastAPI, query sanitising, content-hash idempotency, the error shape, the correlation-ID contract).
* What you changed manually (and why):
  * I chose Turso to try it out, then went back to SQLite FTS5 once the smoke test showed Turso's limitations (ADR 0001).
  * I asked for a provider-agnostic OpenAI-compatible client instead of one provider (ADR 0002).
  * Ingest takes JSONL instead of a JSON body, because it's human-readable and suits bulk upload.
  * One `IncidentService` instead of three service classes.
  * The Customer Draft gets its own LLM call with an isolated, allowlisted context, so internal details can't leak (ADR 0003). The original recommendation was a prompt instruction plus an ID check.
  * LLM errors are opaque to API users and logged in detail.
* Any follow up prompt(s) (copy/paste if important):

  ```
  26. we should go one step further and ensure that the context used to generate the
  `customer_draft` does not have any of those internal identifiers to guarantee that
  nothing can leak. Even if this requires a separate LLM call it is necessary to
  guarantee that the LLM can't accidentally leak any internal details.
  ```

---

**Prompt #2**

* Tool used: Claude Code (Claude Opus 5.5)
* Goal: Slice the work into prioritised tickets.
* Prompt (copy/paste):

  ```
  Before starting work we should slice up the whole effort into tickets with the following priority:
  1. App scaffolding (including logging with `structlog` library, and config with `pydantic-settings`)
  2. /ingest endpoint + inciddent generation script
  3. Each endpoint has its own ticket. Let me know if I missed anything.
  ```
* Result summary: It proposed 8 tickets and added the pieces I had missed: a separate LLM-client ticket, a stretch `/debug/trace` ticket and a final docs pass. It also moved incident details ahead of search, so ingest can be checked sooner.
* What you kept: All 8 tickets.
* What you changed manually (and why): The decision on the trace ticket is deferred. If it's skipped, its approach still goes into `TRADEOFFS.md`.

---

**Prompt #3**

* Tool used: Claude Code (Claude Opus 5.5)
* Goal: Implement ticket 01: scaffolding, config, logging, correlation IDs and the error shape.
* Prompt (copy/paste):

  ```
  got it, start ticket 01
  ```
* Result summary: It built the app factory, `Settings`, structlog JSON logging, the correlation-ID middleware, error handlers and 8 tests, then smoke-tested the real server.
* What you kept: The middleware design, the error shape, structlog config and the tests.
* What you changed manually (and why):
  * Made `__main__.py` a thin wrapper, with `load_settings()` exiting by itself. I also added `uv run start` through `[project.scripts]` for ease of use.
  * Moved routes to `routes.py`, which is the API layer for all future endpoints.
  * Removed an over-built loop that collected settings problems into a list. Pydantic's own message plus a one-line hint is enough. Settings fields are now upper case, so they match the env var names exactly.
  * Added `example.env`, so setup is `cp example.env .env`.
  * Made `LLM_API_KEY` optional, which reverses the earlier "fail at startup" decision. Search and ingest should work without an LLM key. The app logs a startup WARNING, and `/assist` fails fast.
* Any follow up prompt(s) (copy/paste if important):

  ```
  Do we really need to build the `problems` array? I was just testing without wrapping
  `Settings()` in a try-except block and the end result is identical. [...]
  Is there a benefit to the try-except?
  ```

  ```
  I changed my mind about the `LLM_API_KEY` requirement. Let's make it optional and fail
  fast if `/assist` is called without the key set. `create_app` will log a warning if it
  is not set.
  ```

---

**Prompt #4**

* Tool used: Claude Code (Claude Opus 5.5)
* Goal: Implement ticket 02: the Incident model, a deterministic generator, the SQLite repository and an idempotent `POST /ingest`.
* Prompt (copy/paste):

  ```
  merged, let's move on to ticket #2
  ```
* Result summary:
  * Built the `Incident` model with enums, a template and Faker generator (byte-identical across runs), and `SqliteIncidentRepository` (upsert, content hash, FTS5 kept in sync).
  * Added `IncidentService.ingest_ndjson` (atomic, errors per line, duplicate-id detection) and a thin async route that reads the body with a size cap and checks the content type.
  * Wrote 18 new tests and smoke-tested with the real dataset.
* What you kept: _(to fill in after review)_
* What you changed manually (and why):
  * Moved connection handling into a dedicated `db.py` (a `connect()` context manager and `init_db()`) and the schema into `schema/*.sql` files, after review found a connection leak in `init_db`.
* Any follow up prompt(s) (copy/paste if important):

  ```
  `with connect(db_path) as conn` commits or rolls back the transaction, but does not close
  the connection. [...] Close it explicitly, such as with contextlib.closing, while
  retaining the transaction handling.

  On a related note I'd like a dedicated `db.py` module which handles the db connections
  using a contextmanager which handles connections automatically. The module should also
  expose an `init_db` function which is called at app startup. The schema should move to a
  directory containing `.sql` files for table definitions
  ```

---

**Prompt #5**

* Tool used: Claude Code (Claude Opus 5.5)
* Goal: Implement ticket 03: `GET /incidents/{id}`.
* Prompt (copy/paste):

  ```
  merged, let's move on to ticket 03
  ```
* Result summary: Added `get` to the repository protocol and the SQLite implementation, `IncidentService.get_incident` (which raises `NotFoundError`), and a thin route. Wrote tests for the service (fake repository) and the API (round trip, 404 shape and 422).
* What you kept: _(to fill in after review)_
* What you changed manually (and why):
  * Switched Incident ids from `INC-00001` strings to strict positive integers stored as `INTEGER PRIMARY KEY`. The prefix added nothing, and a `TEXT` key costs a hidden unique index.
* Any follow up prompt(s) (copy/paste if important):

  ```
  I have 2 comments related to the incident id:
  1. The `ID_PATTERN` is more generic than what is actually generated in practice, namely `INC-[0-9]+`.
  2. For such a simple service, the "INC-" prefix is taking up extra space with no benefit. It's probably
  far more efficient to use sequential integers. At scale that prefix can add up. Why did you choose that
  pattern over a simple integer primary key?
  ```

---

**Prompt #6**

* Tool used: Claude Code (Claude Opus 5.5)
* Goal: Implement ticket 04: `GET /search` with text search, filters, ranking and pagination.
* Prompt (copy/paste):

  ```
  I already recreated the db. Ticket 03 has been merged, we can move on to ticket 04.
  ```
* Result summary:
  * Added `build_match_query()`, which turns user text into quoted terms joined with OR.
  * Added `SqliteIncidentRepository.search` (weighted BM25, `snippet()`, filters, `json_each` for tags, a shared count) and `IncidentService.search` (400 when there's no `q` and no filter).
  * `SearchQuery` is used directly as FastAPI's query-parameter model.
  * Wrote 38 new tests and smoke-tested against the real dataset.
* What you kept: _(to fill in after review)_
* What you changed manually (and why): _(to fill in after review)_

---

**Prompt #7**

* Tool used: Claude Code (Claude Opus 5.5), with the `claude-api` skill and the live Anthropic docs
* Goal: Implement ticket 05: a provider-agnostic LLM client with strict JSON output, a timeout, and opaque error mapping.
* Prompt (copy/paste):

  ```
  Ticket 04 was merged, please start ticket 05.
  ```
* Result summary:
  * Built `OpenAICompatibleClient`: a provider-to-base-URL mapping with an `LLM_BASE_URL` override, `response_format` with a strict `json_schema` generated from the Pydantic output model, `max_completion_tokens=16000`, `max_retries=1`, and no sampling parameters.
  * Every SDK error, unusable output and missing key maps to `AssistUnavailable` (503) and is logged once at ERROR.
  * Added a fake client for ticket 06 and 18 tests that use a stub SDK.
* What you kept: _(to fill in after review)_
* What you changed manually (and why):
  * Replaced the hand-rolled `SimpleNamespace` SDK stub with `unittest.mock.MagicMock`. It's the standard tool, and `call_args` and `assert_called_once` replace custom recording code. Running the file alone then exposed a test that depended on order: it parsed JSON logs, which are only configured once `create_app()` has run. That was fixed with an autouse `configure_logging` fixture.
  * Added `google` (Gemini) as a third `LLM_PROVIDER`. That's one entry in the base-URL map, plus the config literal, a test and docs. The base URL was checked against Google's compatibility page. Its strict `json_schema` and `max_completion_tokens` support aren't documented there, so they're listed as unverified in `TRADEOFFS.md`.
  * Switched the default provider to OpenAI (`LLM_PROVIDER=openai`, `LLM_MODEL=gpt-6-luna`) after trouble with the Anthropic account. Anthropic and Google remain selectable.
  * After review, handled an empty `choices` list and `APIResponseValidationError`, and added an `OpenAIError` catch-all. All of them previously escaped as unhandled 500s instead of a 503.
  * Dropped multi-provider support, after the Anthropic account issues, and moved from Chat Completions to the OpenAI Responses API (`responses.parse`). `LLM_PROVIDER` was removed and `store=False` added. ADR 0004 supersedes ADR 0002. A live call was confirmed.
* Any follow up prompt(s) (copy/paste if important):

  ```
  I configured an API key. Let's simplify the LLM client and only use openai as the LLM provider.
  We can remove the LLM_PROVIDER config since it won't be needed anymore. We should also be using
  the Responses API instead of Chat Completions since we don't need to worry about compatibility
  with Anthropic and Google Gemini
  ```

  ```
  response.choices[0] raises IndexError if the provider returns a valid response with no choices.
  That escapes the client's error mapping and becomes an unhandled 500, contrary to the ticket's
  requirement that invalid output raise AssistUnavailable and return a 503. Handle an empty choices
  list explicitly. Consider also mapping SDK response-validation errors to AssistUnavailable.
  ```

---

**Prompt #8**

* Tool used: Claude Code (Claude Opus 5.5)
* Goal: Plan the largest ticket (06, `POST /assist`) as focused commits, starting with Candidate retrieval.
* Prompt (copy/paste):

  ```
  Since this is the larget ticket, let's split it up the current ticket into multiple focused commits.
  1. candidate retrieval with a canned response at the API layer. We can use INFO and DEBUG level statements to verify results.
  2. Customer facing draft response based on the cited incidents
  ```
* Result summary:
  * The AI pointed out that the split was missing a step: the internal LLM call and guardrail validator that produce the Cited Incidents step 2 depends on. We agreed on 3 commits in one PR.
  * Commit 1 adds `IncidentService.find_candidates()`, which uses the same `search()` path, logs `tool:search` input and output at INFO and Candidate details at DEBUG, and fails fast when there's no key.
  * It also adds a canned `POST /assist` response, one `OpenAIClient` per app injected into the service, and 7 tests.
  * Commit 2 adds the internal analysis call (`TriageAnalysis`, a strict schema) over the full Candidate records, deduplicated by title.
  * It also adds `apply_guardrail()`, a pure function that drops citations of Incidents the model wasn't shown, collapses duplicates and caps at 5. The insufficient paths are zero Candidates (no LLM call), the model's own flag, and no valid citations left. Generator templates are tagged with an Impact Category, and there are 15 new tests.
  * A live run cited the right SSO Incident in 4.2s and correctly reported insufficient context for an unrelated question.
* Any follow up prompt(s) (copy/paste if important):

  ```
  Finding — [P2] The guardrail checks cited IDs, but not factual claims in the response
  The API returns the model's free-text reason and next_steps directly. apply_guardrail() verifies
  that cited IDs were shown, but it cannot prevent the model from adding unsupported incident details
  to those fields. [...] Ground the returned text in verifiable evidence, or use safe, fixed next
  steps where claims cannot be checked.
  ```

  The result: verbatim evidence excerpts verified in code, and next steps built from stored resolutions (ADR 0005).
* Commit 3 (`start commit 3`):
  * Adds the Customer Draft call with the allowlisted context from `build_draft_context()`: customer-safe enums plus the Cited Incidents' allowlisted fields, with no ids, no question and no uncited Candidates.
  * Adds `internal_identifier_matches()`, a backstop for hostnames, IPs, emails and URLs that falls back to a safe draft. The insufficient paths use a fixed draft with no second call.
  * Adds README sections for Assist and Troubleshooting, and 13 new tests. A live run returned careful drafts with no identifiers.
* Open question, recorded in TRADEOFFS: how strict the draft and next steps should be is a product decision. The draft is reviewed by an operator against the Cited Incidents.
* Follow-up review: added a check for the 120-word limit (the prompt alone didn't enforce it), broadened hostname detection beyond a fixed list of suffixes, and moved IP detection to `ipaddress`, which also covers IPv6. A rejected draft is replaced with the safe draft, and the reasons are logged.

  ```
  - The prompt requests at most 120 words, but CustomerDraft.draft has no length constraint and the
    returned text is not checked. [...] Validate the word count and use the safe canned draft when it
    exceeds the limit.
  - The hostname regex only recognizes a fixed list of suffixes, so it misses addresses such as
    status.example.ca and intranet.example.uk. The IP check covers IPv4 only [...] consider using
    broader hostname detection and Python's ipaddress module for IP literals.
  ```
* What you kept: _(to fill in after review)_
* What you changed manually (and why): _(to fill in after review)_

---

**Prompt #9**

* Tool used: Claude Code (Claude Opus 5.5)
* Goal: Re-check the implementation against the brief before the final docs pass, and act on an external review's findings.
* Prompt (copy/paste):

  ```
  Before moving on to ticket 08, I'm re-reading the assignment and I believe that the intention for
  the /assist endpoint was to make the `search` functionality used by `/search` into a function call,
  rather than a structured response. Please review the requirements and suggest changes to ensure the
  requirements align with the implementation.
  ```
* Result summary:
  * The AI compared each requirement with the code and flagged two gaps:
    * Retrieval wasn't model-driven, so it proposed tool calling with `search_incidents`.
    * The opaque 503 didn't meet "return a clear error explaining what to set and how to run it".
  * Three tickets came out of this and the review: 09 (tool calling), 10 (actionable errors) and 11 (templated Customer Draft, closing review finding 1).
* Any follow up prompt(s) (copy/paste if important):

  ```
  I also had a reviewer run against `main` and it returned with the findings found in
  `.scratch/incident-triage/review-findings.md` please take a look and see if we need additional
  tickets to address these findings.
  ```

  ```
  I'm re-thinking ticket 11. [...] I am wondering if we can refine the instructions on the
  `customer_draft` to ensure that the draft itself is not talking about the the incident itself but
  rather focuses on one of the following:
  - request additional details
  - inform customers that a fix has been delivered or is being worked on
  - inform customers that the issue is being investigated and we will get back to them
  On the other hand these cases are narrow enough that we could use prepared templates instead of an
  LLM call. It would also make the response more deterministic which is helpful for testing.
  ```
* What you kept: the requirement-by-requirement gap list and the ticket split. Ticket 10 (actionable errors) is implemented as proposed.
* What you changed manually (and why):
  * For the draft, chose prepared templates over a stricter prompt, because templates are deterministic and verifiable. The coarse templates are accepted as prototype scope.
  * Kept 1–5 citations instead of padding to 3.
  * Skipped ticket 07 for time.

---

**Prompt #10**

* Tool used: Claude Code (Claude Opus 5.5)
* Goal: Close review finding 1 (the Customer Draft could invent incident details) in ticket 11.
* Prompt (copy/paste):

  ```
  I'm re-thinking ticket 11. The framing of the requirement is that incident details should not be
  made up. I am wondering if we can refine the instructions on the `customer_draft` to ensure that
  the draft itself is not talking about the the incident itself but rather focuses on one of the
  following:
  - request additional details
  - inform customers that a fix has been delivered or is being worked on
  - inform customers that the issue is being investigated and we will get back to them

  On the other hand these cases are narrow enough that we could use prepared templates instead of
  an LLM call. It would also make the response more deterministic which is helpful for testing.
  ```
* Result summary:
  * The AI recommended templates over tighter instructions, because a prompt alone can't enforce anything. It also flagged that a past incident's resolution says nothing about whether *this* customer's problem is fixed. So the fix replies are allowed only when the question says so, with `investigating` as the default.
  * Implementation (ADR 0006): `customer_reply` replaces the three draft enums. This removed the second LLM call, the allowlist and the identifier backstop, about 220 lines in all. A live run picked `investigating` and `fix_in_progress` correctly.
* Any follow up prompt(s) (copy/paste if important):

  ```
  The narrower templates are fine for a prototype which we can consider to be the case for this
  project. For a production application we could expand templates to be more granular, we don't
  need to worry about that here.
  ```
* PR review follow-up:

  ```
  Finding — [P2] The default Customer Reply can claim an investigation is underway without evidence
  When the model selects investigating—the prompt's default for "anything else"—the template tells
  the customer, "We're investigating the issue." [...] Consider a neutral acknowledgment as the
  default, and use the investigating template only when the input confirms investigation is underway.
  ```

  The result: a neutral `acknowledged` default, and `investigating` now gets the same "only if the question says so" rule as the fix replies. An 8-question live run gave the expected reply for every question.
* What you kept: the templates (five after review), the status-claim rule, and removing the backstop and the draft call.
* What you changed manually (and why): _(to fill in after review)_

---

**Prompt #11**

* Tool used: Claude Code (Claude Opus 5.5)
* Goal: Implement ticket 09, where the model retrieves Candidates through function calling instead of a fixed search.
* Prompt (copy/paste):

  ```
  Ticket 10 has been merged in, let's start working on ticket 09
  ```

  (It was ticket 11's PR that had just merged. Ticket 09 depends on it.)
* Result summary:
  * The AI read the installed `openai` SDK before writing the loop. It found `pydantic_responses_function_tool`, the `parsed_arguments` on function calls, and how the SDK's own runner replays output items. With `store=False`, the reasoning items with their `encrypted_content`, the message phases and the function calls all have to be resent each turn.
  * `LLMClient.complete()` became `run_tools()`. The first turn must call the tool, later turns may, and the turn after 3 calls must answer. The `search_incidents` handler calls `IncidentService.search()`, applies the engineer's filters on top of the model's, and feeds invalid arguments back to the model as an error.
  * A 10-question live run worked end to end, with 2–4 LLM calls and 3.7–9.6s per assist. It also showed that the insufficient paths didn't log a `draft` step, which the README's QA loop relies on, so that was fixed.
* Any follow up prompt(s) (copy/paste if important):

  ```
  is ticket 09 completed?
  ```

  The AI said no: the docs, the regression check and the commit were still outstanding.
* What you kept: the explicit loop over the SDK's `max_tool_calls`, enforcing the engineer's filters, and the README QA table extended to retrieval cases.
* What you changed manually (and why): _(to fill in after review)_

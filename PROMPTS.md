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

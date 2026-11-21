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
* What you kept: _(to fill in after review)_
* What you changed manually (and why): _(to fill in after review)_

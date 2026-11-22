# Prompts

These are eight prompts from the build, chosen because each one shows a decision: pushing back on an AI suggestion, questioning generated code, or revising an earlier decision. Prompts that only said "start the next ticket" are left out. Reviewer findings pasted into a prompt came from Codex, set up in prompt #2. Obvious typos in the quoted prompts are corrected.

---

**Prompt #1**

* Tool used: Claude Code (Claude Opus 5.5), with the `grill-with-docs` skill
* Goal: Turn the brief into a shared design before writing any code. Settle each decision, record the domain vocabulary, and record the hard-to-reverse choices.
* Prompt (copy/paste):

  ```
  /mattpocock-skills:grill-with-docs incident-triage-take-home.md
  ```
* Result summary:
  * About 38 questions over 6 rounds, each with a recommended answer.
  * It produced `GLOSSARY.md`, ADRs 0001–0003 and a spec, then sliced the work into tickets.
  * Sub-agents researched Turso and Anthropic's OpenAI compatibility, and it smoke-tested `pyturso` in a scratch venv.
* What you kept: most recommendations: FastAPI, sanitising search input, content-hash idempotency, one error shape, and the correlation-ID contract.
* What you changed manually (and why):
  * I chose Turso to try it out. I went back to SQLite FTS5 once the smoke test showed that Turso skips the FTS index when the query is a bound parameter, so every score is `0.0` (ADR 0001).
  * Ingest takes JSONL instead of a JSON body, because it's human-readable and suits bulk upload.
  * No SQLAlchemy, but a repository pattern, so the API and database layers stay separate.
  * The Customer Draft gets its own LLM call with an isolated, allowlisted context, so internal details can't leak (ADR 0003). The AI had recommended a prompt instruction plus an ID check.
* Any follow up prompt(s) (copy/paste if important):

  ```
  11,12. based on the findings related to Turso, let's go back to SQLite with FTS5. I would normally
  prefer Postgres but for this take-home assignment simplicity wins, and I would only choose Postgres
  if deployability was a concern (make the local environment as close to prod as possible).
  ```

  ```
  26. we should go one step further and ensure that the context used to generate the
  `customer_draft` does not have any of those internal identifiers to guarantee that nothing can
  leak. Even if this requires a separate LLM call it is necessary to guarantee that the LLM can't
  accidentally leak any internal details.
  ```
* Link to code/commit: [`GLOSSARY.md`](GLOSSARY.md), [`docs/adr/`](docs/adr/)

---

**Prompt #2**

* Tool used: Codex (OpenAI's coding agent, `gpt-6-luna`), in a separate chat from the build
* Goal: Set up a second reviewer that checks each ticket against its acceptance criteria and reviews the code for correctness and needless complexity.
* Prompt (copy/paste):

  ```
  I will ask you to review work on the tickets contained in `.scratch/incident-triage/issues`. For
  each ticket verify it against the acceptance criteria. You should also do a code review for
  correctness, and ensuring clean, maintainable code with no unnecessary abstractions.
  ```
* Result summary:
  * Codex reviewed each ticket's changes against the ticket's acceptance criteria, then for correctness and needless complexity.
  * Its findings were pasted verbatim into Claude Code. Examples are the connection leak (#4), the escaped SDK errors (#6), the normalized-logging finding (#7) and the Customer Reply default (#8). Others caught a whitespace-only question passing validation, and guardrails that checked IDs but not claims, or the prompt's word limit but not the output.
  * When there was nothing to fix, it said so. For example, on ticket 04:

    ```
    I found no actionable issues in the staged ticket 04 changes. Query terms are quoted and bound
    to FTS5, ranking weights and description snippets match the ticket, and the filters,
    pagination, and validation appear consistent with the acceptance criteria.
    ```
* What you kept: the reviewer setup for the whole build, with the ticket files as the shared specification for both agents.
* What you changed manually (and why): I still reviewed every change myself, and decided which findings to act on. For example, I first deferred how strict the Customer Draft had to be as a product decision. I reversed that once an external review of `main` showed it was a hard-requirement gap.

---

**Prompt #3**

* Tool used: Claude Code (Claude Opus 5.5)
* Goal: Keep the scaffolding minimal. The AI had wrapped `Settings()` in a try/except that collected each problem into a list before exiting.
* Prompt (copy/paste):

  ```
  Do we really need to build the `problems` array? Letting the pydantic exception bubble up after calling `Settings()`
  without a try-except block displays the same details and has the same result. The app crashes before it can start up.

  [traceback ending in: pydantic_core._pydantic_core.ValidationError: 1 validation error for
  Settings / llm_api_key / Field required]

  Is there a reason why we should keep the try-except block?
  ```
* Result summary: The AI agreed that Pydantic's own message already lists each invalid field. The only real benefit of catching the error was a clean exit without a traceback, which needs a single `sys.exit(...)` with a one-line hint, not a loop.
* What you kept: the one-line `sys.exit` with Pydantic's message.
* What you changed manually (and why):
  * Settings fields are upper case, so they match the env var names exactly.
  * Later I reversed my own earlier decision that a missing `LLM_API_KEY` fails at startup. Ingest and search don't need an LLM, so the key became optional: the app logs a startup WARNING, and only `/assist` fails, fast.
* Any follow up prompt(s) (copy/paste if important):

  ```
  I changed my mind about the `LLM_API_KEY` requirement. Let's make it optional and fail fast if
  `/assist` is called without the key set. `create_app` will log a warning if it is not set.
  ```
* Link to code/commit: [PR #1](https://github.com/jpenasosa/incident-triage/pull/1)

---

**Prompt #4**

* Tool used: Claude Code (Claude Opus 5.5)
* Goal: Fix a connection leak found in review, and restructure database access so it can't happen again.
* Prompt (copy/paste):

  ```
  We have one review comment:
  - @src/incident_triage/repository.py:  with connect(db_path) as conn commits or rolls back the
  transaction, but does not close the connection. Each create_app() call therefore leaves a
  connection open; repeated app creation in tests or reloads can accumulate them. Close it
  explicitly, such as with contextlib.closing, while retaining the transaction handling.

  On a related note I'd like a dedicated `db.py` module which handles the db connections using a
  contextmanager which handles connections automatically. The module should also expose an
  `init_db` function which is called at app startup. The schema should move to a directory
  containing `.sql` files for table definitions
  ```
* Result summary:
  * The AI had assumed `with sqlite3.connect(...)` closes the connection. In fact it only commits or rolls back.
  * The fix is a `db.connect()` context manager that always closes, with transactions handled separately (`with connect(db) as conn, conn:`).
  * `init_db()` applies `schema/*.sql` in order on a new database and reports whether it did.
  * A test checks that the connection is closed afterwards.
* What you kept: the `connect()` and `init_db()` design and the test.
* What you changed manually (and why):
  * `init_db()` skips an existing database file and returns `True` or `False`, so `create_app()` logs `database_initialized` only when it actually created one. The README documents deleting the file after a schema change, which suits fast prototyping.
  * A module-level `logger` in `create_app()`, so the startup warning and info lines share it.
* Link to code/commit: [PR #2](https://github.com/jpenasosa/incident-triage/pull/2)

---

**Prompt #5**

* Tool used: Claude Code (Claude Opus 5.5)
* Goal: Challenge the AI's choice of string Incident ids such as `INC-00001`.
* Prompt (copy/paste):

  ```
  I have 2 comments related to the incident id:
  1. The `ID_PATTERN` is more generic than what is actually generated in practice, namely `INC-[0-9]+`.
  2. For such a simple service, the "INC-" prefix is taking up extra space with no benefit. It's
   more efficient to use integers. At scale that prefix can add up. Why did you choose that pattern
   over a simple integer primary key?
  ```
* Result summary: The AI admitted it had copied the look of ticketing systems without weighing it. In SQLite, a `TEXT PRIMARY KEY` also creates a hidden unique index, while `INTEGER PRIMARY KEY` is the rowid itself, so the FTS rows can share the id directly.
* What you kept: the switch to strict positive integers stored as `INTEGER PRIMARY KEY`, validated the same way in the model, the route and the generator.
* What you changed manually (and why): nothing beyond the prompt. The question itself was the change.
* Link to code/commit: [PR #3](https://github.com/jpenasosa/incident-triage/pull/3)

---

**Prompt #6**

* Tool used: Claude Code (Claude Opus 5.5)
* Goal: Revise my earlier provider-agnostic design (ADR 0002) once the constraints that justified it had changed, and move to the API that fits a single provider.
* Prompt (copy/paste):

  ```
  I configured an API key. Let's simplify the LLM client and only use openai as the LLM provider.
  We can remove the LLM_PROVIDER config since it won't be needed anymore. We should also be using
  the Responses API instead of Chat Completions since we don't need to worry about compatibility
  with Anthropic and Google Gemini
  ```
* Result summary:
  * The client calls `responses.parse(text_format=Model)`, which derives a strict JSON schema from the Pydantic model and validates the reply. Requests send `store=False`.
  * `LLM_PROVIDER` and the provider-to-base-URL map are gone. `LLM_BASE_URL` stays as an optional proxy override.
  * ADR 0004 supersedes ADR 0002, and a live call was confirmed.
* What you kept: the Responses API client, the error mapping to one `AssistUnavailable`, and testing with a `MagicMock` SDK.
* What you changed manually (and why):
  * The OpenAI-compatible layer was my own design, for flexibility across providers. When account problems ruled out Anthropic and I decided to stick to OpenAI, the flexibility no longer paid for itself, so I removed it rather than keep dead configuration.
  * Moving to the Responses API was my suggestion, after we had switched over to the OpenAI client. We had only used Chat Completions because it was the API compatible with Anthropic and Google Gemini. With a single provider, the Responses API was the more correct choice: it's intended to supersede Chat Completions, and it supports function calling and structured responses. Function calling later carried ticket 09 (ADR 0007).
  * Earlier review findings (an empty `choices` list, and an unmapped `APIResponseValidationError`, both of which escaped as 500s) shaped the error mapping that carried over.
* Link to code/commit: [PR #5](https://github.com/jpenasosa/incident-triage/pull/5)

---

**Prompt #7**

* Tool used: Claude Code (Claude Opus 5.5)
* Goal: Re-read the brief before the final docs pass and question whether `/assist` met its intent, not just its letter.
* Prompt (copy/paste):

  ```
  At the moment I think we will skip ticket 07 due to time constraints. Before moving on to ticket
  08, I'm re-reading the assignment and I believe that the intention for the /assist endpoint was
  to make the `search` functionality used by `/search` into a function call, rather than a
  structured response. Please review the requirements and suggest changes to ensure the
  requirements align with the implementation.
  ```
* Result summary:
  * The AI compared each requirement with the code and agreed that the "tool step" wording points to function calling.
  * It found a second gap I hadn't asked about: the opaque 503 didn't "return a clear error explaining what to set and how to run it".
  * Both became tickets:
    * **Ticket 09:** the model drives retrieval through a strict `search_incidents` tool backed by the same `search()` path. Engineer filters win, and citations are checked against every search (ADR 0007).
    * **Ticket 10:** each LLM failure returns a fixed, actionable message and a `details` reason.
* What you kept: both tickets as proposed, with the guardrail unchanged over the union of search results.
* What you changed manually (and why):
  * I first chose actionable messages for configuration errors only. When the external review also flagged timeouts, ticket 10 was widened to every failure category.
  * Kept 1–5 citations instead of padding to 3, because padding would mean citing irrelevant Incidents.
  * Skipped the optional trace endpoint for time.
  * Ran a separate review of `main` against the brief and turned its findings into tickets.
* Any follow up prompt(s) (copy/paste if important):

  ```
  I also had a reviewer run against `main` and it returned with the findings found in
  `.scratch/incident-triage/review-findings.md` please take a look and see if we need additional
  tickets to address these findings.
  ```

  ```
  - [P2] Log the normalized filters that the search actually uses — service.py. The tool:search
    input is logged before SearchQuery normalizes tags. [...] Construct the query first and log its
    normalized q and filters.
  ```

  That review fix also led the AI to the same problem in `overridden`, which compared raw values. Both were fixed.
* Link to code/commit: [PR #7](https://github.com/jpenasosa/incident-triage/pull/7), [PR #9](https://github.com/jpenasosa/incident-triage/pull/9)

---

**Prompt #8**

* Tool used: Claude Code (Claude Opus 5.5)
* Goal: Close the review finding that the model-written Customer Draft could invent incident details, and revisit my own earlier design for it (ADR 0003).
* Prompt (copy/paste):

  ```
  I'm re-thinking ticket 11. The framing of the requirement is that incident details should not be
  made up. I am wondering if we can refine the instructions on the `customer_draft` to ensure that
  the draft itself is not talking about the incident itself but rather focuses on one of the
  following:
  - request additional details
  - inform customers that a fix has been delivered or is being worked on
  - inform customers that the issue is being investigated and we will get back to them

  On the other hand these cases are narrow enough that we could use prepared templates instead of
  an LLM call. It would also make the response more deterministic which is helpful for testing.
  ```
* Result summary:
  * The AI recommended templates, because a prompt alone can't enforce anything.
  * It flagged that a past incident's resolution says nothing about whether *this* customer's problem is fixed. So the status replies are allowed only when the question says so.
  * The model now picks a `customer_reply` and code returns fixed text (ADR 0006, superseding ADR 0003).
  * The second LLM call, the allowlist and the identifier backstop are gone, about 220 lines in all.
  * A question table in the README checks the reply choice live.
* What you kept: the templates, the status-claim rule, and removing the draft call and the backstop.
* What you changed manually (and why):
  * Accepted coarse templates as prototype scope.
  * Asked for the QA questions in the README, so the reply choice can be checked by hand.
* Any follow up prompt(s) (copy/paste if important):

  ```
  The narrower templates are fine for a prototype which we can consider to be the case for this
  project. For a production application we could expand templates to be more granular, we don't
  need to worry about that here.
  ```

  PR review:

  ```
  Finding — [P2] The default Customer Reply can claim an investigation is underway without evidence
  When the model selects investigating—the prompt's default for "anything else"—the template tells
  the customer, "We're investigating the issue." [...] Consider a neutral acknowledgment as the
  default, and use the investigating template only when the input confirms investigation is underway.
  ```

  The result: a neutral `acknowledged` default, and `investigating` gets the same "only if the question says so" rule as the fix replies.
* Link to code/commit: [PR #8](https://github.com/jpenasosa/incident-triage/pull/8)

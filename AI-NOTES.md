# AI Notes

## How AI was used

I used Claude Code (Claude Opus 5.5) for the whole build. I set the scope, made the decisions and reviewed every change.

- **Design before code:** a structured "grilling" session, with the `grill-with-docs` skill from the `mattpocock-skills@claude-plugins-official` plugin, turned the brief into about 38 decisions. Each one came with the AI's recommended answer, and I accepted, changed or overruled it. The results are in `GLOSSARY.md`, the ADRs and a spec, and the AI then sliced the work into tickets.
- **Process:**
  - Every ticket had its own branch and PR.
  - The AI proposed each commit message and waited for my approval before committing.
  - Each PR was reviewed by me and by Codex, OpenAI's coding agent running `gpt-6-luna`, as a second reviewer. Findings were pasted back verbatim for the AI to fix.
  - Near the end, a separate reviewer checked `main` against the brief. Its findings became tickets 09–11.
- **Research:**
  - Sub-agents read the Turso and Anthropic docs, and every claim was then checked by running real code.
  - For the tool-calling loop, the AI read the installed `openai` SDK's source instead of relying on memory. That's how it found the details needed to replay turns with `store=False`.
- **Implementation:**
  - Scaffolding, the repository, the search query builder, the LLM client and the assist pipeline were written by the AI ticket by ticket.
  - I made design changes through review: integer ids, `db.py`, an optional API key, the provider and API switch, templates instead of a draft call, and tool calling.
- **Tests:** written alongside each change. They're usually red first for bug fixes, and each regression test was checked to fail with its fix removed.
- **Docs:** the README, ADRs and these three files were updated with each ticket rather than at the end, and every example output comes from a real run.

## Where AI output was wrong or weak

1. **The Turso research was incomplete and contradicted itself.** The docs summary gave conflicting statements about which way `fts_score` ranks, and it missed the biggest problem. A smoke test in a scratch venv found that when the query is a bound parameter (`?`), Turso silently skips the FTS index and every score is `0.0`. Keeping ranking would have meant putting user text directly into SQL. That finding is why we went back to SQLite FTS5 (ADR 0001).
2. **The first Customer Draft guardrail was too weak.** The AI proposed a prompt instruction ("no internal identifiers") plus a check that no Incident ID appears in the draft. I pushed back: the draft call must never *see* internal details. That became a separate LLM call with an allowlisted context (ADR 0003).
3. **Passing tests hid a packaging bug.** After ticket 01 the tests passed, but `python -m incident_triage` failed with `No module named incident_triage`. The project had been installed while `src/` was still empty, and pytest's `pythonpath = ["src"]` hid it. Only running the real entry point caught it. The fix was `uv sync --reinstall-package incident-triage`.
4. **A circular import in the first draft.** `errors` and `observability` imported each other. The AI first "fixed" it with an import inside a function, then replaced that with a clean dependency direction: `observability` depends on `errors`.
5. **It followed a warning towards an unverified package.** A Starlette deprecation warning suggested replacing `httpx` with `httpx2`, and the AI tried to make the swap. The tool's safety check blocked it, because the package couldn't be verified. We kept `httpx`. Later, in ticket 05, the official `openai` SDK turned out to depend on `httpx2`. That confirmed the package was legitimate, and it silenced the warning without any swap. Blocking an unverified swap was still the right call at the time.
6. **The tag normalisation didn't work.** The AI used `StringConstraints(to_lower=True, pattern=...)` and assumed the input would be lowercased before the pattern check. Pydantic checks the pattern against the raw input, so `"DB"` was rejected instead of becoming `"db"`. A unit test written for that exact case caught it, and the fix normalises with a `BeforeValidator`.
7. **A connection leak hid behind a common Python misconception.** The AI wrote `with sqlite3.connect(...) as conn:` in `init_db`, assuming it closes the connection. For `sqlite3`, the context manager only commits or rolls back. Each `create_app()` left a connection open. Code review caught it. The fix is a `db.connect()` context manager that always closes, with the transaction handled separately (`with connect(db) as conn, conn:`). A test now checks that the connection is closed afterwards.
8. **The AI picked an id format without weighing it.** It generated `INC-00001`-style string ids, copying the look of ticketing systems, and validated them with a pattern looser than the data. Review asked why it wasn't a plain integer, and the AI couldn't give a real reason. In SQLite, a `TEXT PRIMARY KEY` also creates a hidden unique index, while `INTEGER PRIMARY KEY` is the rowid itself. We switched to strict positive integers.
9. **Rounding hid every score.** The AI rounded BM25 scores to 4 decimals. In a small test corpus, FTS5's IDF is clamped to almost zero, so every score showed `0.0` and the ranking test failed. The test caught it, and the fix keeps full precision. Ordering was never affected.
10. **The error mapping had gaps around the edges.** The LLM client mapped every SDK error it listed to a 503, but two paths escaped as unhandled 500s. `response.choices[0]` raised `IndexError` on a valid response with no choices. `APIResponseValidationError` sits beside `APIStatusError` in the SDK's error hierarchy, so the `except` chain never caught it. Code review found both. The fix adds an explicit empty-choices check, that exception, and a final `OpenAIError` catch-all. Each new test was confirmed to fail with its fix removed.
11. **The first guardrail only checked IDs.** The AI's first version of the citation guardrail checked that cited IDs had been shown to the model, but still returned the model's free-text reasons and next steps. Those could contain invented details, which the brief forbids as a hard requirement. Code review flagged it. The fix makes the model quote evidence, which code verifies word for word against the record, and builds next steps from stored resolution summaries (ADR 0005). A live run confirmed the model quotes exactly, with no citations dropped.
12. **The output checks were narrower than they looked.** The draft prompt asked for at most 120 words, but nothing checked the output. The hostname check only knew 10 suffixes, so it missed `.ca`, `.uk` and others, and the IP check was IPv4-only. Code review caught all three. The fix adds a word-count check, a generic hostname pattern and IP detection through `ipaddress`, covering IPv6, brackets, zones and ports. Running the new cases through the old function confirmed it missed all five of them.
13. **The allowlist addressed leaks, not invention.** ADR 0003 kept Internal Identifiers away from the draft call, but the model still wrote the draft, so it could claim details the records don't support. The AI recorded this as an "open product decision" instead of fixing it. An external review against the brief flagged it as a hard-requirement gap. The fix replaces the draft call with prepared templates chosen by a model-picked Customer Reply (ADR 0006).
14. **The first template default still claimed a status.** The AI made `investigating` the fallback reply, so "We're investigating the issue" went out even when nothing said anyone was. It was the same mistake as the fix replies, in a milder form, and the AI had guarded only those. PR review caught it. The fix adds a neutral `acknowledged` default and applies the "only if the question says so" rule to every reply that states a status.
15. **A regression check that proved nothing.** To show a new test fails without its fix, the AI ran `git stash`, the tests, then `git stash pop`. The stash set aside the whole branch, tests included, so "17 passed" was `main`'s own tests running against `main`'s code. The AI caught this and said so, confirmed nothing was lost, and redid the check by removing only the one log line: the test then failed as it should.

## How changes were validated

- **Tests and lint:** `uv run pytest` (158 tests) and `ruff` on every change. Service tests use fakes for the repository and the model, so the guardrails and the tool loop are tested without a network. The LLM client is tested against a stubbed SDK.
- **Regression tests checked to fail first:** for each review fix, the new test was run against the code without the fix. Once, that check was done wrongly (item 15) and then redone properly.
- **Live runs after each feature:**
  - The real server and the real OpenAI API, checking curl output and the JSON logs.
  - The 10-question QA table in the README was run as documented, and its "Observed" column keeps only what held across two runs.
  - Error paths (missing key, bad key, unknown model) were triggered live, which costs nothing because OpenAI rejects them before using any tokens.
- **A clean clone before submission:** every README command was run from a fresh clone of `main`. It found one stale sample output, now fixed.
- **Scratch experiments for third-party claims,** such as Turso's FTS behaviour, instead of trusting a summary of the docs.
- **Two reviewers on every PR,** me and Codex, plus one external review of the whole repository against the brief.

## Prompt patterns that worked

- **Grill before building:** every question comes with a recommended answer, so most decisions take a single "yes", and the hard ones get real discussion.
- **Ask the AI to compare the code with the brief:** "review the requirements and suggest changes" found the tool-calling gap and the opaque-error gap, and an external review found the draft-grounding gap. Each became a ticket.
- **Paste review findings verbatim:** the AI fixes the stated problem, writes a test that fails without the fix, and often finds the same bug elsewhere. For example, the normalized-logging finding led to the same fix in `overridden`.
- **Approval gates:** "no commits without my approval" and one PR per ticket kept each change small enough to review, and made the AI state what it had verified before each commit.
- **Ask for status directly:** asking "is ticket 09 completed?" got an honest list of what was left instead of an assumption that the work was done.
- **Make claims checkable:** prefer designs where code can verify the model's output (verbatim evidence, enum choices, templates) over prompt instructions that can't be enforced.

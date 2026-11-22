# Incident Triage

A search API and grounded AI assistant over past Incidents. Built for the take-home brief in [`incident-triage-take-home.md`](incident-triage-take-home.md).

Vocabulary: [`GLOSSARY.md`](GLOSSARY.md). Key decisions: [`docs/adr/`](docs/adr/).

## Requirements

- [uv](https://docs.astral.sh/uv/). It installs Python 3.12 automatically if needed.

## Configuration

All configuration is through environment variables. A `.env` file in the repo root is also read, and `example.env` is a template for it. Variables already set in the environment take precedence over `.env`.

| Variable | Default | Notes |
| --- | --- | --- |
| `LLM_API_KEY` | none | OpenAI API key. Only `/assist` needs it. Without it, a warning is logged at startup and `/assist` returns 503. |
| `LLM_MODEL` | `gpt-6-luna` | OpenAI model id |
| `LLM_BASE_URL` | `api.openai.com` | Optional: a proxy or Azure OpenAI endpoint |
| `LLM_TIMEOUT_SECONDS` | `20` | Per LLM call, 1–120, with one retry. An assist makes 2–4 calls. |
| `DB_PATH` | `data/incidents.db` | SQLite file. Delete it after schema changes (see [Database and schema changes](#database-and-schema-changes)) |
| `PORT` | `8000` | |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |

## Start

```bash
uv sync
cp example.env .env   # set LLM_API_KEY in .env to use /assist
uv run start
```

```bash
curl -i http://localhost:8000/healthz
```

Every response carries an `X-Correlation-ID` header. To use your own ID, send the header; otherwise one is generated. Every log line for that request includes the ID.

## Data

The dataset is synthetic and isn't committed. Generate it before the first ingest. It's built from curated failure templates and a pinned `faker`, with seed 42, so every run writes the same 300 Incidents to `data/incidents.jsonl`, byte for byte:

```bash
uv run python scripts/generate_incidents.py          # options: --count 300 --seed 42 --out data/incidents.jsonl
```

### Database and schema changes

The SQLite database lives at `DB_PATH` (default `data/incidents.db`). The schema is applied **only when that file doesn't exist yet**: the first start creates it and logs `database_initialized`. There is no migrations tool, so **after changing anything in `src/incident_triage/schema/`, delete the database and ingest again**:

```bash
rm data/incidents.db
uv run start        # recreates the database with the new schema
# then ingest again (see below)
```

Delete the file in the same way if the first start failed part-way, because a half-initialised file is never re-initialised.

## Ingest

`POST /ingest` takes newline-delimited JSON (one Incident per line), up to 5 MB and 1,000 lines:

```bash
curl -s -X POST http://localhost:8000/ingest \
  -H 'Content-Type: application/x-ndjson' \
  --data-binary @data/incidents.jsonl   # generate it first (see Data)
# {"ingested":300,"updated":0,"skipped":0}
```

It's idempotent. Records are matched on `id`, and a hash of their content decides whether a known record counts as `updated` (changed) or `skipped` (unchanged). Running it a second time gives `{"ingested":0,"updated":0,"skipped":300}`.

The batch is atomic. If any line is invalid, nothing is written, and the 422 response lists up to 50 problems as `{line, field, message}`. An `id` repeated within one batch counts as a problem. A wrong `Content-Type` gives 415, and an oversized body gives 413.

## Search

`GET /search` runs a ranked full-text search over title, description, resolution summary and tags, with optional filters:

```bash
# text search
curl -s -G http://localhost:8000/search --data-urlencode "q=sso login failing"

# text + filters (tags repeat, and every tag must match)
curl -s -G http://localhost:8000/search --data-urlencode "q=timeout" \
  -d environment=prod -d severity=SEV2 -d tags=report

# filters only (newest first), paginated
curl -s "http://localhost:8000/search?service=benefits&tags=enrollment&limit=5&offset=5"
```

The response looks like this (the first of 10 results shown):

```json
{"total": 62, "limit": 10, "offset": 0,
 "results": [{"id": 281, "title": "Users unable to log in via single sign-on",
              "snippet": "SSO logins failed with an 'invalid assertion' error for 203 minutes. About 79% of login attempts were affected.",
              "score": 7.785935897292315, "service": "auth", "environment": "dev", "severity": "SEV2",
              "created_at": "2026-10-08T00:26:59Z"}, ...]}
```

- **Ranking:** results match **any** word of `q`, ranked by BM25. A match in the title counts most (×3), then tags (×2), then description and resolution summary (×1). Equal scores are ordered newest first. `score` is higher for more relevant results and `null` for filter-only searches.
- **Input handling:** `q` is treated as plain words. Search syntax such as quotes, `AND`, `NEAR`, `title:` or `*` has no special meaning.
- **Filters:** `environment`, `service` and `severity` take one value each. Repeat `tags` to require several.
- **Limits:** `limit` is 1–50 (default 10), `offset` is ≥ 0, and `q` is at most 500 characters.
- **Errors:** with neither `q` nor a filter, the response is 400. Invalid or unknown parameters return 422.

## Incident details

`GET /incidents/{id}` returns the full Incident record:

```bash
curl -s http://localhost:8000/incidents/42
# {"id":42,"created_at":"2026-02-12T08:27:30Z","environment":"prod","service":"timekeeping",
#  "severity":"SEV3","title":"Timesheet approval button unresponsive", ... ,"tags":["approvals","ui","regression"]}
```

Incident ids are positive integers supplied by the ingested data. An unknown id returns 404 `NOT_FOUND`, and an id that isn't a positive 64-bit integer returns 422.

## Assist

`POST /assist` finds past Incidents relevant to a question, says why each one is relevant, suggests next steps and drafts a customer reply. It needs `LLM_API_KEY`.

```bash
curl -s -X POST http://localhost:8000/assist \
  -H 'Content-Type: application/json' \
  -H 'X-Correlation-ID: my-trace-1' \
  -d '{"question": "Customers report SSO login fails with an invalid assertion error", "environment": "prod"}'
```

```json
{"relevant_incidents": [{"id": 258, "title": "Users unable to log in via single sign-on",
   "reason": "Record states: “SSO logins failed with an 'invalid assertion' error”"}],
 "next_steps": ["Incident 258 was resolved by: An expired signing certificate was rotated. Added certificate expiry monitoring with a 30-day warning.",
                "Confirm the affected service, environment and start time match the cited incidents.",
                "Check for recent deployments or configuration changes in the affected service."],
 "customer_draft": "Thank you for reaching out. To help us investigate, could you tell us which part of the product is affected, when the problem started, the exact error message you see, and roughly how many users are affected?",
 "insufficient_context": false}
```

The body takes `question` (1–2000 characters, not blank) plus the same optional filters as search: `environment`, `service`, `severity` and `tags`.

**How it works.** Each step is logged under the request's correlation ID:

1. **`llm:analyze`:** the LLM gets the question and any filters you set, plus a `search_incidents` tool (ADR 0007).
2. **`tool:search`:** each tool call runs the same search as `GET /search` with the model's query and returns the full records of the top 10 Candidates. Candidates with the same title are collapsed. The model must search at least once and may search up to 3 times, for example with other words when the first search finds nothing useful. Filters in your request always win over the model's, and an override is logged as `overridden`. The model then picks up to 5 relevant Incidents and quotes evidence from each one.
3. **`guardrail`:** code drops any citation of an Incident the model wasn't shown, and any whose quote isn't in the stored record. `reason` is that verified quote, and `next_steps` quote the stored resolutions plus fixed steps (ADR 0005).
4. **`draft`:** `customer_draft` is a prepared template chosen by the model's `customer_reply`: `request_details`, `acknowledged`, `investigating`, `fix_in_progress` or `fix_delivered` (ADR 0006). No model writes customer-facing text. The default is the neutral `acknowledged`. Replies that state a status (`investigating`, `fix_in_progress`, `fix_delivered`) are chosen only when the question says so. In the example above, the question gives no start time, so the draft asks for details. Assist makes 2–4 LLM calls: one per search, plus the answer.

**When nothing relevant is found** (no search returned anything, the model says so, or no citation survives the guardrail), the response has `insufficient_context: true`, no Incidents, and next steps and a draft that ask for more information.

### Validating the Customer Reply

These questions exercise each Customer Reply, plus two retrieval cases (rows 9 and 10). The "Observed" column is from two live runs against the generated dataset with `gpt-6-luna`. The model makes the choice, so other models or prompt changes may differ. Treat a different result as something to investigate, not necessarily a bug.

| # | Body | Expected | Observed |
| --- | --- | --- | --- |
| 1 | `{"question": "why can't I log in?", "service": "auth", "severity": "SEV1"}` | `request_details`: no error, time or scale given | `request_details` |
| 2 | `{"question": "why can't I log in?"}` | `request_details` | `request_details` |
| 3 | `{"question": "Customers report SSO login fails with an invalid assertion error since 9am", "environment": "prod"}` | `acknowledged`: nothing says anyone is investigating yet | `acknowledged` |
| 4 | `{"question": "Hourly employees' overtime was calculated at the wrong rate in this week's payroll run"}` | `acknowledged` | `acknowledged` |
| 5 | `{"question": "Customers report SSO login fails with an invalid assertion error since 9am; the auth team is looking into it now", "environment": "prod"}` | `investigating` | `investigating` |
| 6 | `{"question": "SSO invalid assertion errors again; we found the expired signing certificate and are rotating it now"}` | `fix_in_progress` | `fix_in_progress` |
| 7 | `{"question": "Customer reported SSO invalid assertion errors yesterday; we rotated the expired certificate this morning and the fix is deployed"}` | `fix_delivered` | `fix_delivered` |
| 8 | `{"question": "Overtime rates were wrong in payroll; the corrected calculation shipped in today's release"}` | `fix_delivered` | `fix_delivered` |
| 9 | `{"question": "Payroll overtime looks wrong for our hourly staff", "service": "timekeeping"}` | `request_details`, and every `tool:search` keeps `service: timekeeping` | `request_details`, with all 3 searches filtered on `timekeeping` |
| 10 | `{"question": "The office coffee machine is leaking water onto the carpet"}` | `request_details` through insufficient `no_candidates` | `request_details`, 2 searches with 0 hits |

The reply matched every row in two live runs. Some details differed between the runs:

- which incident was cited
- how many searches the model ran (1 to 3)
- whether rows 2 and 9 cited an incident or took the insufficient path. Both give `request_details`.

The response contains the draft text, not the reply name. The `draft` log line records the choice under the request's correlation ID, for example `{"step": "draft", "customer_reply": "fix_in_progress", "source": "template", ...}`. If the server's output is saved to `server.log`, this loop sends the ten questions as `qa-1` to `qa-10` and prints the reply chosen for each one. The `draft` step is logged on the insufficient paths too. The `tool:search` lines show the model's queries:

```bash
uv run start > server.log 2>&1 &   # with data ingested

n=0
while IFS= read -r body; do
  n=$((n+1))
  curl -s -o /dev/null -X POST http://localhost:8000/assist \
    -H 'Content-Type: application/json' -H "X-Correlation-ID: qa-$n" -d "$body"
done <<'EOF'
{"question": "why can't I log in?", "service": "auth", "severity": "SEV1"}
{"question": "why can't I log in?"}
{"question": "Customers report SSO login fails with an invalid assertion error since 9am", "environment": "prod"}
{"question": "Hourly employees' overtime was calculated at the wrong rate in this week's payroll run"}
{"question": "Customers report SSO login fails with an invalid assertion error since 9am; the auth team is looking into it now", "environment": "prod"}
{"question": "SSO invalid assertion errors again; we found the expired signing certificate and are rotating it now"}
{"question": "Customer reported SSO invalid assertion errors yesterday; we rotated the expired certificate this morning and the fix is deployed"}
{"question": "Overtime rates were wrong in payroll; the corrected calculation shipped in today's release"}
{"question": "Payroll overtime looks wrong for our hourly staff", "service": "timekeeping"}
{"question": "The office coffee machine is leaking water onto the carpet"}
EOF

grep '"step": "draft"' server.log | grep -o '"customer_reply": "[a-z_]*".*"correlation_id": "qa-[0-9]*"'
```

Each assist makes 2–4 LLM calls, so the loop makes about 20–40.

### Troubleshooting assist

A failed assist call returns 503 `ASSIST_UNAVAILABLE`. The message says what to set or do, and `details` gives a machine-readable `reason`. For example, without `LLM_API_KEY`:

```json
{"error":{"code":"ASSIST_UNAVAILABLE","message":"Assist needs an OpenAI API key. Set LLM_API_KEY in .env and restart the server with `uv run start`.","correlation_id":"t10-check","details":[{"reason":"llm_key_missing"}]}}
```

| `reason` | Typical cause | Fix |
| --- | --- | --- |
| `llm_key_missing` | `LLM_API_KEY` not set. Startup also logs the WARNING `llm_api_key_not_set`. | Set `LLM_API_KEY` in `.env` and restart. |
| `llm_key_rejected` | OpenAI returned 401 or 403. | Set a valid key and restart. |
| `llm_model_unknown` | OpenAI returned 404. | Set `LLM_MODEL` to a model your key can use (check `LLM_BASE_URL` if set) and restart. |
| `llm_timeout` | No reply within `LLM_TIMEOUT_SECONDS`. | Retry, or raise `LLM_TIMEOUT_SECONDS` (maximum 120) and restart. |
| `llm_rate_limited` | OpenAI returned 429. | Retry later. Check usage limits and billing. |
| `llm_upstream_error` | 5xx or other HTTP errors, network failures, or unusable output. | Retry later, then check `LLM_BASE_URL` and the server log. |

The response never includes the provider's error text. The exact cause is in the server log. Find the ERROR line `llm_call_failed` with the same `correlation_id`:

```bash
uv run start 2>&1 | grep '"llm_call_failed"'
```

To see which Candidates were considered, run with `LOG_LEVEL=DEBUG` and look for `assist_candidates`.

## Errors

Every error has the same shape:

```json
{"error": {"code": "NOT_FOUND", "message": "Not Found", "correlation_id": "…"}}
```

## Tests

```bash
uv run pytest
uv run ruff check . && uv run ruff format --check .
```

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
| `LLM_TIMEOUT_SECONDS` | `20` | |
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

The response looks like this:

```json
{"total": 62, "limit": 10, "offset": 0,
 "results": [{"id": 17, "title": "Users unable to log in via single sign-on",
              "snippet": "SSO logins failed with an 'invalid assertion' error…", "score": 7.786,
              "service": "auth", "environment": "prod", "severity": "SEV2",
              "created_at": "2026-01-19T10:19:20Z"}]}
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
   "reason": "Record states: “SSO logins failed with an 'invalid assertion' error for 226 minutes.”"}],
 "next_steps": ["Incident 258 was resolved by: An expired signing certificate was rotated. Added certificate expiry monitoring with a 30-day warning.",
                "Confirm the affected service, environment and start time match the cited incidents.",
                "Check for recent deployments or configuration changes in the affected service."],
 "customer_draft": "We’re investigating the login access issue. No action is needed from you right now, and we’ll share an update when we have more information.",
 "insufficient_context": false}
```

The body takes `question` (1–2000 characters, not blank) plus the same optional filters as search: `environment`, `service`, `severity` and `tags`.

**How it works.** Each step is logged under the request's correlation ID:

1. **`tool:search`:** the question runs through the same search as `GET /search`, returning the top 10 Candidates. Candidates with the same title are collapsed.
2. **`llm:analyze`:** the LLM picks up to 5 relevant Incidents and quotes evidence from each one.
3. **`guardrail`:** code drops any citation of an Incident the model wasn't shown, and any whose quote isn't in the stored record. `reason` is that verified quote, and `next_steps` quote the stored resolutions plus fixed steps (ADR 0005).
4. **`llm:draft`:** a second LLM call writes `customer_draft` from customer-safe fields only (ADR 0003). It never sees Incident ids or the engineer's question. If the draft looks like it contains a hostname, IP, email or URL, it's replaced with a safe fixed reply.

**When nothing relevant is found** (no search hits, the model says so, or no citation survives the guardrail), the response has `insufficient_context: true`, no Incidents, and next steps and a draft that ask for more information.

### Troubleshooting assist

Every assist failure returns the same response to API users:

```json
{"error": {"code": "ASSIST_UNAVAILABLE", "message": "Assist service is not accessible", "correlation_id": "…"}}
```

The cause is in the server log. Find the ERROR line `llm_call_failed` with the same `correlation_id`:

```bash
uv run start 2>&1 | grep '"llm_call_failed"'
```

| `cause` in the log | Fix |
| --- | --- |
| `LLM_API_KEY is not set` | Set `LLM_API_KEY` in `.env` and restart. Startup also logs the WARNING `llm_api_key_not_set`. |
| `OpenAI rejected LLM_API_KEY (HTTP 401)` | Use a valid key and restart. |
| `OpenAI returned HTTP 404` | `LLM_MODEL` is probably not a model your key can use. |
| `OpenAI returned HTTP 429` | Rate limit or quota. Retry later or check billing. |
| `timed out after 20.0s` | Raise `LLM_TIMEOUT_SECONDS` (maximum 120). |
| `could not reach OpenAI` | Network or proxy problem. Check `LLM_BASE_URL` if you set it. |

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

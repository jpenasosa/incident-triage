# Incident Triage

A search API and grounded AI assistant over past Incidents. Built for the take-home brief in [`incident-triage-take-home.md`](incident-triage-take-home.md).

Vocabulary: [`GLOSSARY.md`](GLOSSARY.md). Key decisions: [`docs/adr/`](docs/adr/).

## Requirements

- [uv](https://docs.astral.sh/uv/). It installs Python 3.12 automatically if needed.

## Configuration

All configuration is through environment variables. A `.env` file in the repo root is also read.

| Variable | Default | Notes |
| --- | --- | --- |
| `LLM_API_KEY` | none (required) | The app refuses to start without it. Use `dummy` to run everything except `/assist`. |
| `LLM_PROVIDER` | `anthropic` | `anthropic` or `openai` |
| `LLM_MODEL` | `claude-sonnet-5-5` | |
| `LLM_BASE_URL` | per provider | Overrides the provider's URL, for any OpenAI-compatible endpoint |
| `LLM_TIMEOUT_SECONDS` | `20` | |
| `DB_PATH` | `data/incidents.db` | SQLite file |
| `PORT` | `8000` | |
| `LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING` or `ERROR` |

## Start

```bash
uv sync
export LLM_API_KEY=<your-provider-key>   # or: export LLM_API_KEY=dummy
uv run python -m incident_triage
```

```bash
curl -i http://localhost:8000/healthz
```

Every response carries an `X-Correlation-ID` header. To use your own ID, send the header; otherwise one is generated. Every log line for that request includes the ID.

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

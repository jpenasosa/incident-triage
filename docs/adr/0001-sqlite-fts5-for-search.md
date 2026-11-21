# SQLite FTS5 for search

We store and search Incidents in SQLite with an FTS5 table (BM25 ranking, `snippet()`), chosen for zero-install simplicity in a timeboxed exercise.

## Considered Options

- **Turso (pyturso 0.8.4)**: rejected after a smoke test. It has no FTS5. Its Tantivy-based FTS needs an experimental flag and has no snippet function. When the query is a bound parameter, the index is silently skipped, which makes every score 0.0. So keeping ranking would mean putting user text directly into SQL.
- **Postgres full-text search**: the better choice if deployability mattered, since it keeps local close to prod. That isn't a concern for this exercise, so simplicity wins.

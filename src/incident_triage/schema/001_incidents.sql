-- Incident records. `id` is the rowid (no separate index). `tags` is a JSON array,
-- used for exact tag filtering.
CREATE TABLE IF NOT EXISTS incidents (
    id INTEGER PRIMARY KEY,
    created_at TEXT NOT NULL,
    environment TEXT NOT NULL,
    service TEXT NOT NULL,
    severity TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    resolution_summary TEXT NOT NULL,
    tags TEXT NOT NULL,
    content_hash TEXT NOT NULL
);

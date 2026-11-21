-- Full-text index over the searchable fields. Rows share their rowid with `incidents`.
CREATE VIRTUAL TABLE IF NOT EXISTS incidents_fts USING fts5(
    title, description, resolution_summary, tags
);

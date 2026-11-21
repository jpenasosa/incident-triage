-- Full-text index over the searchable fields. Each row's rowid is the Incident `id`.
CREATE VIRTUAL TABLE IF NOT EXISTS incidents_fts USING fts5(
    title, description, resolution_summary, tags
);

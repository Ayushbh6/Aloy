ALTER TABLE provider_sessions ADD COLUMN updated_at TEXT;
    UPDATE provider_sessions SET updated_at=strftime('%Y-%m-%dT%H:%M:%f+00:00','now')
        WHERE updated_at IS NULL;

CREATE TABLE spend_events (
        source_type TEXT NOT NULL CHECK(source_type IN ('run','audio')),
        source_id TEXT NOT NULL, amount_usd REAL NOT NULL CHECK(amount_usd >= 0),
        created_at TEXT NOT NULL, PRIMARY KEY(source_type, source_id)
    );
    INSERT INTO spend_events
        SELECT 'run', id, estimated_usd, started_at FROM runs
        WHERE estimated_usd IS NOT NULL AND estimated_usd > 0;
    INSERT INTO spend_events
        SELECT 'audio', id, estimated_usd, created_at FROM audio_assets
        WHERE estimated_usd IS NOT NULL AND estimated_usd > 0;

CREATE TABLE conversations (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, system_prompt TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE runs (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        provider TEXT NOT NULL, model TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('running','completed','failed','cancelled','interrupted')),
        started_at TEXT NOT NULL, ended_at TEXT, error TEXT,
        input_tokens INTEGER, output_tokens INTEGER, estimated_usd REAL
    );
    CREATE TABLE messages (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
        sequence INTEGER NOT NULL, role TEXT NOT NULL CHECK(role IN ('user','assistant')),
        text TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('complete','failed','cancelled')),
        created_at TEXT NOT NULL, UNIQUE(conversation_id, sequence)
    );
    CREATE INDEX messages_order ON messages(conversation_id, sequence);
    CREATE TABLE audio_assets (
        id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        message_id TEXT REFERENCES messages(id) ON DELETE SET NULL,
        run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
        path TEXT NOT NULL UNIQUE, format TEXT NOT NULL, duration_seconds REAL,
        sha256 TEXT NOT NULL, direction TEXT NOT NULL CHECK(direction IN ('input','output')),
        playback_status TEXT NOT NULL DEFAULT 'ready', created_at TEXT NOT NULL
    );
    CREATE TABLE provider_sessions (
        conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
        provider TEXT NOT NULL, session_id TEXT NOT NULL, synced_sequence INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(conversation_id, provider)
    );
    CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE voice_timings (
 id INTEGER PRIMARY KEY,
 operation_id TEXT,
 run_id TEXT,
 stage TEXT NOT NULL,
 source TEXT NOT NULL,
 monotonic_seconds REAL NOT NULL,
 value REAL,
 created_at TEXT NOT NULL
);
CREATE INDEX voice_timings_operation ON voice_timings(operation_id, id);

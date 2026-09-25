CREATE VIRTUAL TABLE summary_fts USING fts5(summary_id UNINDEXED, conversation_id UNINDEXED, text);
INSERT INTO summary_fts SELECT id,conversation_id,text FROM conversation_summaries;
CREATE TRIGGER summary_fts_insert AFTER INSERT ON conversation_summaries
BEGIN
 INSERT INTO summary_fts VALUES(NEW.id,NEW.conversation_id,NEW.text);
END;
CREATE TRIGGER summary_fts_delete AFTER DELETE ON conversation_summaries
BEGIN
 DELETE FROM summary_fts WHERE summary_id=OLD.id;
END;
ALTER TABLE media_assets RENAME TO media_assets_v1;
CREATE TABLE media_assets (
 id TEXT PRIMARY KEY,
 conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
 run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
 kind TEXT NOT NULL CHECK(kind IN ('image','video','audio')),
 path TEXT NOT NULL UNIQUE,
 mime_type TEXT NOT NULL,
 bytes INTEGER NOT NULL,
 sha256 TEXT NOT NULL,
 duration_seconds REAL,
 created_at TEXT NOT NULL
);
INSERT INTO media_assets SELECT * FROM media_assets_v1;
DROP TABLE media_assets_v1;

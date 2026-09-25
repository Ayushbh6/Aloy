CREATE TABLE run_steps (
 id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
 sequence INTEGER NOT NULL,
 kind TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','completed','failed','cancelled','interrupted')),
 name TEXT,
 arguments_json TEXT,
 result_json TEXT,
 error TEXT,
 started_at TEXT NOT NULL,
 ended_at TEXT,
 UNIQUE(run_id,sequence)
);
ALTER TABLE provider_sessions ADD COLUMN context_revision INTEGER NOT NULL DEFAULT 0;
ALTER TABLE provider_sessions ADD COLUMN tool_hash TEXT NOT NULL DEFAULT '';
CREATE TABLE provider_calls (
 id TEXT PRIMARY KEY,
 run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
 purpose TEXT NOT NULL CHECK(purpose IN ('main','maintenance','web_search','vision')),
 provider TEXT NOT NULL,
 model TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN ('running','completed','failed','cancelled','interrupted')),
 reservation_id TEXT REFERENCES reservations(id) ON DELETE SET NULL,
 input_tokens INTEGER,
 output_tokens INTEGER,
 estimated_usd REAL,
 started_at TEXT NOT NULL,
 ended_at TEXT
);
CREATE TABLE media_assets (
 id TEXT PRIMARY KEY,
 conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
 run_id TEXT REFERENCES runs(id) ON DELETE SET NULL,
 kind TEXT NOT NULL CHECK(kind IN ('image','video')),
 path TEXT NOT NULL UNIQUE,
 mime_type TEXT NOT NULL,
 bytes INTEGER NOT NULL,
 sha256 TEXT NOT NULL,
 duration_seconds REAL,
 created_at TEXT NOT NULL
);
CREATE TABLE web_sources (
 id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
 title TEXT NOT NULL,
 url TEXT NOT NULL,
 snippet TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE artifacts (
 id TEXT PRIMARY KEY,
 run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
 version INTEGER NOT NULL,
 kind TEXT NOT NULL,
 payload_json TEXT NOT NULL,
 created_at TEXT NOT NULL
);
CREATE TABLE memories (
 id TEXT PRIMARY KEY,
 text TEXT NOT NULL,
 category TEXT NOT NULL,
 scope TEXT NOT NULL CHECK(scope IN ('global','conversation')),
 conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE,
 source_message_id TEXT REFERENCES messages(id) ON DELETE CASCADE,
 evidence_type TEXT NOT NULL CHECK(evidence_type IN ('user_statement','observed','inferred')),
 confidence REAL NOT NULL CHECK(confidence>=0 AND confidence<=1),
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','forgotten')),
 created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL
);
CREATE TABLE conversation_summaries (
 id TEXT PRIMARY KEY,
 conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
 revision INTEGER NOT NULL,
 through_sequence INTEGER NOT NULL,
 text TEXT NOT NULL,
 created_at TEXT NOT NULL,
 UNIQUE(conversation_id,revision)
);
CREATE TABLE index_outbox (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 entity_type TEXT NOT NULL CHECK(entity_type IN ('message','memory','summary')),
 entity_id TEXT NOT NULL,
 operation TEXT NOT NULL CHECK(operation IN ('upsert','delete')),
 created_at TEXT NOT NULL,
 indexed_at TEXT
);
CREATE INDEX index_outbox_pending ON index_outbox(indexed_at,id);
CREATE VIRTUAL TABLE message_fts USING fts5(message_id UNINDEXED, conversation_id UNINDEXED, text);
CREATE VIRTUAL TABLE memory_fts USING fts5(memory_id UNINDEXED, text);
INSERT INTO message_fts(message_id,conversation_id,text)
 SELECT id,conversation_id,text FROM messages WHERE status='complete';
INSERT INTO index_outbox(entity_type,entity_id,operation,created_at)
 SELECT 'message',id,'upsert',strftime('%Y-%m-%dT%H:%M:%f+00:00','now')
 FROM messages WHERE status='complete';
CREATE TRIGGER message_fts_insert AFTER INSERT ON messages WHEN NEW.status='complete'
BEGIN
 INSERT INTO message_fts(message_id,conversation_id,text) VALUES(NEW.id,NEW.conversation_id,NEW.text);
 INSERT INTO index_outbox(entity_type,entity_id,operation,created_at)
 VALUES('message',NEW.id,'upsert',strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
END;
CREATE TRIGGER message_fts_update AFTER UPDATE OF text,status ON messages
BEGIN
 DELETE FROM message_fts WHERE message_id=OLD.id;
 INSERT INTO message_fts(message_id,conversation_id,text)
 SELECT NEW.id,NEW.conversation_id,NEW.text WHERE NEW.status='complete';
 INSERT INTO index_outbox(entity_type,entity_id,operation,created_at)
 VALUES('message',NEW.id,CASE WHEN NEW.status='complete' THEN 'upsert' ELSE 'delete' END,
 strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
END;
CREATE TRIGGER message_fts_delete AFTER DELETE ON messages
BEGIN
 DELETE FROM message_fts WHERE message_id=OLD.id;
 INSERT INTO index_outbox(entity_type,entity_id,operation,created_at)
 VALUES('message',OLD.id,'delete',strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
END;
CREATE TRIGGER memory_fts_insert AFTER INSERT ON memories WHEN NEW.status='active'
BEGIN
 INSERT INTO memory_fts(memory_id,text) VALUES(NEW.id,NEW.text);
 INSERT INTO index_outbox(entity_type,entity_id,operation,created_at)
 VALUES('memory',NEW.id,'upsert',strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
END;
CREATE TRIGGER memory_fts_update AFTER UPDATE OF text,status ON memories
BEGIN
 DELETE FROM memory_fts WHERE memory_id=OLD.id;
 INSERT INTO memory_fts(memory_id,text) SELECT NEW.id,NEW.text WHERE NEW.status='active';
 INSERT INTO index_outbox(entity_type,entity_id,operation,created_at)
 VALUES('memory',NEW.id,CASE WHEN NEW.status='active' THEN 'upsert' ELSE 'delete' END,
 strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
END;
CREATE TRIGGER memory_fts_delete AFTER DELETE ON memories
BEGIN
 DELETE FROM memory_fts WHERE memory_id=OLD.id;
 INSERT INTO index_outbox(entity_type,entity_id,operation,created_at)
 VALUES('memory',OLD.id,'delete',strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
END;
CREATE TRIGGER summary_index_insert AFTER INSERT ON conversation_summaries
BEGIN
 INSERT INTO index_outbox(entity_type,entity_id,operation,created_at)
 VALUES('summary',NEW.id,'upsert',strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
END;
CREATE TRIGGER summary_index_delete AFTER DELETE ON conversation_summaries
BEGIN
 INSERT INTO index_outbox(entity_type,entity_id,operation,created_at)
 VALUES('summary',OLD.id,'delete',strftime('%Y-%m-%dT%H:%M:%f+00:00','now'));
END;

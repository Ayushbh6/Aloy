CREATE TABLE reservations (
 id TEXT PRIMARY KEY, amount_usd REAL NOT NULL CHECK(amount_usd>=0),
 status TEXT NOT NULL CHECK(status IN ('held','dispatched','settled')),
 created_at TEXT NOT NULL
);
CREATE TABLE pending_deletions(path TEXT PRIMARY KEY);
CREATE TRIGGER audio_run_conversation BEFORE INSERT ON audio_assets
WHEN NEW.run_id IS NOT NULL AND NOT EXISTS (
 SELECT 1 FROM runs WHERE id=NEW.run_id AND conversation_id=NEW.conversation_id
) BEGIN SELECT RAISE(ABORT, 'audio run conversation mismatch'); END;
CREATE TRIGGER audio_message_conversation BEFORE INSERT ON audio_assets
WHEN NEW.message_id IS NOT NULL AND NOT EXISTS (
 SELECT 1 FROM messages WHERE id=NEW.message_id AND conversation_id=NEW.conversation_id
) BEGIN SELECT RAISE(ABORT, 'audio message conversation mismatch'); END;

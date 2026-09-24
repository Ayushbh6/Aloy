CREATE UNIQUE INDEX one_running_turn ON runs(conversation_id) WHERE status='running';
CREATE TRIGGER audio_run_conversation_update BEFORE UPDATE OF run_id,conversation_id ON audio_assets
WHEN NEW.run_id IS NOT NULL AND NOT EXISTS (
 SELECT 1 FROM runs WHERE id=NEW.run_id AND conversation_id=NEW.conversation_id
) BEGIN SELECT RAISE(ABORT, 'audio run conversation mismatch'); END;
CREATE TRIGGER audio_message_conversation_update BEFORE UPDATE OF message_id,conversation_id ON audio_assets
WHEN NEW.message_id IS NOT NULL AND NOT EXISTS (
 SELECT 1 FROM messages WHERE id=NEW.message_id AND conversation_id=NEW.conversation_id
) BEGIN SELECT RAISE(ABORT, 'audio message conversation mismatch'); END;

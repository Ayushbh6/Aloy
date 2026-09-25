ALTER TABLE artifacts ADD COLUMN conversation_id TEXT REFERENCES conversations(id) ON DELETE CASCADE;
ALTER TABLE artifacts ADD COLUMN sequence INTEGER NOT NULL DEFAULT 0;
ALTER TABLE artifacts ADD COLUMN operation_id TEXT NOT NULL DEFAULT '';
UPDATE artifacts
SET conversation_id=(SELECT conversation_id FROM runs WHERE runs.id=artifacts.run_id);
UPDATE artifacts
SET sequence=(
 SELECT COUNT(*) FROM artifacts AS prior
 WHERE prior.run_id=artifacts.run_id
 AND (prior.created_at<artifacts.created_at OR
      (prior.created_at=artifacts.created_at AND prior.id<=artifacts.id))
);
CREATE INDEX artifacts_conversation_sequence ON artifacts(conversation_id,sequence);

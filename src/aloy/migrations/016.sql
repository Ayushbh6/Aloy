CREATE TABLE harness_checkpoints (
 id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
 through_sequence INTEGER NOT NULL, status TEXT NOT NULL, content TEXT NOT NULL,
 source_ids TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE task_states (
 id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
 objective TEXT NOT NULL, status TEXT NOT NULL, checkpoint TEXT NOT NULL DEFAULT '{}',
 last_run_id TEXT, updated_at TEXT NOT NULL
);
CREATE INDEX task_conversation ON task_states(conversation_id, updated_at);
CREATE TABLE learning_evidence (
 id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
 run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
 skill TEXT NOT NULL, evidence_type TEXT NOT NULL, answer TEXT NOT NULL,
 feedback TEXT NOT NULL, source_message_id TEXT NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
 audio_id TEXT REFERENCES audio_assets(id) ON DELETE SET NULL, created_at TEXT NOT NULL
);
CREATE TABLE tool_evidence (
 id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
 run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE, name TEXT NOT NULL,
 content TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE terminal_sessions (
 id TEXT PRIMARY KEY, conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
 run_id TEXT NOT NULL, command TEXT NOT NULL, cwd TEXT NOT NULL, pid INTEGER,
 status TEXT NOT NULL, exit_code INTEGER, output_path TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE file_changes (
 id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
 path TEXT NOT NULL, before_path TEXT, before_hash TEXT, after_hash TEXT, created_at TEXT NOT NULL
);
CREATE TABLE active_capabilities (
 conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
 name TEXT NOT NULL, content TEXT NOT NULL, PRIMARY KEY(conversation_id,name)
);
INSERT INTO settings(key,value) VALUES('host_access','full') ON CONFLICT(key) DO UPDATE SET value='full';
UPDATE settings SET value=json_insert(value,'$[#]','read','$[#]','glob','$[#]','grep','$[#]','edit','$[#]','apply_patch','$[#]','terminal','$[#]','terminal_control','$[#]','context_retrieve','$[#]','capability_search','$[#]','capability_control') WHERE key='tools' AND json_valid(value);
CREATE TABLE harness_goals (
 id TEXT PRIMARY KEY, title TEXT NOT NULL, objective TEXT NOT NULL,
 status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
ALTER TABLE task_states ADD COLUMN goal_id TEXT REFERENCES harness_goals(id) ON DELETE SET NULL;

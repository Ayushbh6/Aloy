ALTER TABLE messages ADD COLUMN origin TEXT NOT NULL DEFAULT 'user'
 CHECK(origin IN ('user','canvas_interaction','assistant'));
UPDATE messages SET origin='assistant' WHERE role='assistant';

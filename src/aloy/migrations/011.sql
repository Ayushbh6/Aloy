CREATE TRIGGER completed_run_index AFTER UPDATE OF status ON runs WHEN NEW.status='completed'
BEGIN
 INSERT INTO index_outbox(entity_type,entity_id,operation,created_at)
 SELECT 'message',id,'upsert',strftime('%Y-%m-%dT%H:%M:%f+00:00','now')
 FROM messages WHERE run_id=NEW.id AND status='complete';
END;

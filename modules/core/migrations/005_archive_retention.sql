-- Archived rows are deleted once they have stayed archived for 30 days
-- (see app/db/retention.py). archived_at is set by triggers when a row is archived
-- and cleared when it is reopened, so reopening restarts the clock. Rows inserted
-- already archived (imports) are stamped by the next purge run: an AFTER INSERT
-- trigger would make SQLite report foreign-key reads that the module guard refuses.
-- The no_delete triggers now allow exactly that and nothing else; the agent still
-- cannot delete. Rows already archived start counting from this migration.

-- Imported rows that were deleted, so a later import does not bring them back.
CREATE TABLE core_purged (
  external_ref TEXT PRIMARY KEY,
  purged_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TRIGGER core_purged_no_delete BEFORE DELETE ON core_purged
BEGIN SELECT RAISE(ABORT, 'rows are never deleted; archive them instead'); END;

ALTER TABLE core_areas ADD COLUMN archived_at TIMESTAMP;
UPDATE core_areas SET archived_at = CURRENT_TIMESTAMP WHERE status = 'archived';
CREATE TRIGGER core_areas_archived_on_update AFTER UPDATE OF status ON core_areas
FOR EACH ROW WHEN (NEW.status = 'archived') IS NOT (OLD.status = 'archived')
BEGIN
  UPDATE core_areas SET archived_at = CASE WHEN NEW.status = 'archived' THEN CURRENT_TIMESTAMP END
  WHERE id = NEW.id;
END;
DROP TRIGGER core_areas_no_delete;
CREATE TRIGGER core_areas_no_delete BEFORE DELETE ON core_areas
FOR EACH ROW WHEN NOT (OLD.status = 'archived' AND coalesce(OLD.archived_at <= datetime('now', '-30 days'), 0))
BEGIN SELECT RAISE(ABORT, 'rows are never deleted until they have been archived for 30 days'); END;

ALTER TABLE core_tasks ADD COLUMN archived_at TIMESTAMP;
UPDATE core_tasks SET archived_at = CURRENT_TIMESTAMP WHERE status = 'archived';
CREATE TRIGGER core_tasks_archived_on_update AFTER UPDATE OF status ON core_tasks
FOR EACH ROW WHEN (NEW.status = 'archived') IS NOT (OLD.status = 'archived')
BEGIN
  UPDATE core_tasks SET archived_at = CASE WHEN NEW.status = 'archived' THEN CURRENT_TIMESTAMP END
  WHERE id = NEW.id;
END;
DROP TRIGGER core_tasks_no_delete;
CREATE TRIGGER core_tasks_no_delete BEFORE DELETE ON core_tasks
FOR EACH ROW WHEN NOT (OLD.status = 'archived' AND coalesce(OLD.archived_at <= datetime('now', '-30 days'), 0))
BEGIN SELECT RAISE(ABORT, 'rows are never deleted until they have been archived for 30 days'); END;

ALTER TABLE core_schedule_blocks ADD COLUMN archived_at TIMESTAMP;
UPDATE core_schedule_blocks SET archived_at = CURRENT_TIMESTAMP WHERE status = 'archived';
CREATE TRIGGER core_schedule_blocks_archived_on_update AFTER UPDATE OF status ON core_schedule_blocks
FOR EACH ROW WHEN (NEW.status = 'archived') IS NOT (OLD.status = 'archived')
BEGIN
  UPDATE core_schedule_blocks SET archived_at = CASE WHEN NEW.status = 'archived' THEN CURRENT_TIMESTAMP END
  WHERE id = NEW.id;
END;
DROP TRIGGER core_schedule_blocks_no_delete;
CREATE TRIGGER core_schedule_blocks_no_delete BEFORE DELETE ON core_schedule_blocks
FOR EACH ROW WHEN NOT (OLD.status = 'archived' AND coalesce(OLD.archived_at <= datetime('now', '-30 days'), 0))
BEGIN SELECT RAISE(ABORT, 'rows are never deleted until they have been archived for 30 days'); END;

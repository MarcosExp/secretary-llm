-- Archived rows are deleted once they have stayed archived for 30 days
-- (see app/db/retention.py). archived_at is set by triggers when a row is archived
-- and cleared when it is reopened, so reopening restarts the clock. Rows inserted
-- already archived (imports) are stamped by the next purge run: an AFTER INSERT
-- trigger would make SQLite report foreign-key reads that the module guard refuses.
-- The no_delete triggers now allow exactly that and nothing else; the agent still
-- cannot delete. Rows already archived start counting from this migration.

ALTER TABLE jobs_applications ADD COLUMN archived_at TIMESTAMP;
UPDATE jobs_applications SET archived_at = CURRENT_TIMESTAMP WHERE stage = 'archived';
CREATE TRIGGER jobs_applications_archived_on_update AFTER UPDATE OF stage ON jobs_applications
FOR EACH ROW WHEN (NEW.stage = 'archived') IS NOT (OLD.stage = 'archived')
BEGIN
  UPDATE jobs_applications SET archived_at = CASE WHEN NEW.stage = 'archived' THEN CURRENT_TIMESTAMP END
  WHERE id = NEW.id;
END;
DROP TRIGGER jobs_applications_no_delete;
CREATE TRIGGER jobs_applications_no_delete BEFORE DELETE ON jobs_applications
FOR EACH ROW WHEN NOT (OLD.stage = 'archived' AND coalesce(OLD.archived_at <= datetime('now', '-30 days'), 0))
BEGIN SELECT RAISE(ABORT, 'rows are never deleted until they have been archived for 30 days'); END;

DROP TRIGGER jobs_application_tasks_no_delete;
CREATE TRIGGER jobs_application_tasks_no_delete BEFORE DELETE ON jobs_application_tasks
FOR EACH ROW WHEN NOT (
  EXISTS (SELECT 1 FROM jobs_applications WHERE id = OLD.application_id AND stage = 'archived' AND coalesce(archived_at <= datetime('now', '-30 days'), 0))
  OR EXISTS (SELECT 1 FROM core_tasks WHERE id = OLD.task_id AND status = 'archived' AND coalesce(archived_at <= datetime('now', '-30 days'), 0)))
BEGIN SELECT RAISE(ABORT, 'rows are never deleted until they have been archived for 30 days'); END;

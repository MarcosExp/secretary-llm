-- Archived rows are deleted once they have stayed archived for 30 days
-- (see app/db/retention.py). archived_at is set by triggers when a row is archived
-- and cleared when it is reopened, so reopening restarts the clock. Rows inserted
-- already archived (imports) are stamped by the next purge run: an AFTER INSERT
-- trigger would make SQLite report foreign-key reads that the module guard refuses.
-- The no_delete triggers now allow exactly that and nothing else; the agent still
-- cannot delete. Rows already archived start counting from this migration.
-- Units go with their course, so a unit of a deletable course is deletable too.

ALTER TABLE studies_programs ADD COLUMN archived_at TIMESTAMP;
UPDATE studies_programs SET archived_at = CURRENT_TIMESTAMP WHERE status = 'archived';
CREATE TRIGGER studies_programs_archived_on_update AFTER UPDATE OF status ON studies_programs
FOR EACH ROW WHEN (NEW.status = 'archived') IS NOT (OLD.status = 'archived')
BEGIN
  UPDATE studies_programs SET archived_at = CASE WHEN NEW.status = 'archived' THEN CURRENT_TIMESTAMP END
  WHERE id = NEW.id;
END;
DROP TRIGGER studies_programs_no_delete;
CREATE TRIGGER studies_programs_no_delete BEFORE DELETE ON studies_programs
FOR EACH ROW WHEN NOT (OLD.status = 'archived' AND coalesce(OLD.archived_at <= datetime('now', '-30 days'), 0))
BEGIN SELECT RAISE(ABORT, 'rows are never deleted until they have been archived for 30 days'); END;

ALTER TABLE studies_courses ADD COLUMN archived_at TIMESTAMP;
UPDATE studies_courses SET archived_at = CURRENT_TIMESTAMP WHERE status = 'archived';
CREATE TRIGGER studies_courses_archived_on_update AFTER UPDATE OF status ON studies_courses
FOR EACH ROW WHEN (NEW.status = 'archived') IS NOT (OLD.status = 'archived')
BEGIN
  UPDATE studies_courses SET archived_at = CASE WHEN NEW.status = 'archived' THEN CURRENT_TIMESTAMP END
  WHERE id = NEW.id;
END;
DROP TRIGGER studies_courses_no_delete;
CREATE TRIGGER studies_courses_no_delete BEFORE DELETE ON studies_courses
FOR EACH ROW WHEN NOT (OLD.status = 'archived' AND coalesce(OLD.archived_at <= datetime('now', '-30 days'), 0))
BEGIN SELECT RAISE(ABORT, 'rows are never deleted until they have been archived for 30 days'); END;

ALTER TABLE studies_units ADD COLUMN archived_at TIMESTAMP;
UPDATE studies_units SET archived_at = CURRENT_TIMESTAMP WHERE status = 'archived';
CREATE TRIGGER studies_units_archived_on_update AFTER UPDATE OF status ON studies_units
FOR EACH ROW WHEN (NEW.status = 'archived') IS NOT (OLD.status = 'archived')
BEGIN
  UPDATE studies_units SET archived_at = CASE WHEN NEW.status = 'archived' THEN CURRENT_TIMESTAMP END
  WHERE id = NEW.id;
END;
DROP TRIGGER studies_units_no_delete;
CREATE TRIGGER studies_units_no_delete BEFORE DELETE ON studies_units
FOR EACH ROW WHEN NOT (OLD.status = 'archived' AND coalesce(OLD.archived_at <= datetime('now', '-30 days'), 0)
  OR EXISTS (SELECT 1 FROM studies_courses WHERE id = OLD.course_id AND status = 'archived' AND coalesce(archived_at <= datetime('now', '-30 days'), 0)))
BEGIN SELECT RAISE(ABORT, 'rows are never deleted until they have been archived for 30 days'); END;

DROP TRIGGER studies_course_tasks_no_delete;
CREATE TRIGGER studies_course_tasks_no_delete BEFORE DELETE ON studies_course_tasks
FOR EACH ROW WHEN NOT (
  EXISTS (SELECT 1 FROM studies_courses WHERE id = OLD.course_id AND status = 'archived' AND coalesce(archived_at <= datetime('now', '-30 days'), 0))
  OR EXISTS (SELECT 1 FROM core_tasks WHERE id = OLD.task_id AND status = 'archived' AND coalesce(archived_at <= datetime('now', '-30 days'), 0)))
BEGIN SELECT RAISE(ABORT, 'rows are never deleted until they have been archived for 30 days'); END;

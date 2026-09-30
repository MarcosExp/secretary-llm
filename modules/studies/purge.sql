-- Deletes courses, units and programs archived for 30 days, and this module's links
-- to tasks that core is about to delete (run by app/db/retention.py, before core).
-- A deleted course takes its units with it; a program waits until no course uses it.

-- Start the clock for rows that were inserted already archived.
UPDATE studies_programs SET archived_at = CURRENT_TIMESTAMP WHERE status = 'archived' AND archived_at IS NULL;
UPDATE studies_courses SET archived_at = CURRENT_TIMESTAMP WHERE status = 'archived' AND archived_at IS NULL;
UPDATE studies_units SET archived_at = CURRENT_TIMESTAMP WHERE status = 'archived' AND archived_at IS NULL;

INSERT OR IGNORE INTO core_purged (external_ref)
SELECT external_ref FROM studies_units
WHERE external_ref IS NOT NULL
  AND ((status = 'archived' AND archived_at <= datetime('now', '-30 days'))
       OR course_id IN (SELECT id FROM studies_courses
                        WHERE status = 'archived' AND archived_at <= datetime('now', '-30 days')));

INSERT OR IGNORE INTO core_purged (external_ref)
SELECT external_ref FROM studies_courses
WHERE external_ref IS NOT NULL
  AND status = 'archived' AND archived_at <= datetime('now', '-30 days');

DELETE FROM studies_course_tasks
WHERE course_id IN (SELECT id FROM studies_courses
                    WHERE status = 'archived' AND archived_at <= datetime('now', '-30 days'))
   OR task_id IN (SELECT id FROM core_tasks
                  WHERE status = 'archived' AND archived_at <= datetime('now', '-30 days'));

DELETE FROM studies_units
WHERE (status = 'archived' AND archived_at <= datetime('now', '-30 days'))
   OR course_id IN (SELECT id FROM studies_courses
                    WHERE status = 'archived' AND archived_at <= datetime('now', '-30 days'));

DELETE FROM studies_courses
WHERE status = 'archived' AND archived_at <= datetime('now', '-30 days');

DELETE FROM studies_programs
WHERE status = 'archived' AND archived_at <= datetime('now', '-30 days')
  AND id NOT IN (SELECT program_id FROM studies_courses WHERE program_id IS NOT NULL);

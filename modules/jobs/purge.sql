-- Deletes applications archived for 30 days, and this module's links to tasks
-- that core is about to delete (run by app/db/retention.py, before core).

-- Start the clock for rows that were inserted already archived.
UPDATE jobs_applications SET archived_at = CURRENT_TIMESTAMP WHERE stage = 'archived' AND archived_at IS NULL;

INSERT OR IGNORE INTO core_purged (external_ref)
SELECT external_ref FROM jobs_applications
WHERE external_ref IS NOT NULL
  AND stage = 'archived' AND archived_at <= datetime('now', '-30 days');

DELETE FROM jobs_application_tasks
WHERE application_id IN (SELECT id FROM jobs_applications
                         WHERE stage = 'archived' AND archived_at <= datetime('now', '-30 days'))
   OR task_id IN (SELECT id FROM core_tasks
                  WHERE status = 'archived' AND archived_at <= datetime('now', '-30 days'));

DELETE FROM jobs_applications
WHERE stage = 'archived' AND archived_at <= datetime('now', '-30 days');

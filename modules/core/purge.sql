-- Deletes rows archived for 30 days (run by app/db/retention.py, after the other
-- modules have removed their links to these rows). Imported rows leave their
-- external_ref in core_purged so a later import does not bring them back.

-- Start the clock for rows that were inserted already archived.
UPDATE core_tasks SET archived_at = CURRENT_TIMESTAMP WHERE status = 'archived' AND archived_at IS NULL;
UPDATE core_schedule_blocks SET archived_at = CURRENT_TIMESTAMP WHERE status = 'archived' AND archived_at IS NULL;
UPDATE core_areas SET archived_at = CURRENT_TIMESTAMP WHERE status = 'archived' AND archived_at IS NULL;

INSERT OR IGNORE INTO core_purged (external_ref)
SELECT external_ref FROM core_tasks
WHERE external_ref IS NOT NULL
  AND status = 'archived' AND archived_at <= datetime('now', '-30 days');

DELETE FROM core_tasks
WHERE status = 'archived' AND archived_at <= datetime('now', '-30 days');

DELETE FROM core_schedule_blocks
WHERE status = 'archived' AND archived_at <= datetime('now', '-30 days');

-- An area waits until no task uses it.
DELETE FROM core_areas
WHERE status = 'archived' AND archived_at <= datetime('now', '-30 days')
  AND id NOT IN (SELECT area_id FROM core_tasks WHERE area_id IS NOT NULL);

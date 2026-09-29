-- Where an imported row came from (e.g. "notion:<page id>"), so imports can run
-- again and only add what is new.

ALTER TABLE core_tasks ADD COLUMN external_ref TEXT;
CREATE UNIQUE INDEX core_tasks_external_ref ON core_tasks (external_ref) WHERE external_ref IS NOT NULL;

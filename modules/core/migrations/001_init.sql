-- Core tables: areas, tasks, fixed schedule blocks and the agent log.
-- Rows are never deleted: they are archived. The *_no_delete triggers enforce it.

CREATE TABLE core_areas (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived'))
);

CREATE TABLE core_tasks (
  id INTEGER PRIMARY KEY,
  title TEXT NOT NULL CHECK (length(trim(title)) > 0),
  status TEXT NOT NULL DEFAULT 'todo' CHECK (status IN ('todo', 'doing', 'done', 'archived')),
  priority TEXT NOT NULL DEFAULT 'P2' CHECK (priority IN ('P1', 'P2', 'P3')),  -- today, this week, someday
  area_id INTEGER REFERENCES core_areas(id),
  due DATE CHECK (due IS NULL OR date(due) IS due),
  estimate_h REAL CHECK (estimate_h IS NULL OR estimate_h > 0),
  notes TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  completed_at TIMESTAMP
);
CREATE INDEX core_tasks_status_due ON core_tasks (status, due);

CREATE TRIGGER core_tasks_touch AFTER UPDATE ON core_tasks
FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
BEGIN
  UPDATE core_tasks SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TABLE core_schedule_blocks (  -- fixed weekly blocks used by the planner
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  kind TEXT NOT NULL,
  weekday INTEGER NOT NULL CHECK (weekday BETWEEN 0 AND 6),  -- 0 = Monday
  start_time TEXT NOT NULL CHECK (start_time GLOB '[0-2][0-9]:[0-5][0-9]' AND time(start_time) IS NOT NULL),  -- HH:MM
  end_time TEXT NOT NULL CHECK (end_time GLOB '[0-2][0-9]:[0-5][0-9]' AND time(end_time) IS NOT NULL AND end_time > start_time),
  valid_from DATE,
  valid_to DATE,
  gcal_event_id TEXT,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived'))
);

CREATE TABLE core_agent_log (  -- traceability and cost
  id INTEGER PRIMARY KEY,
  ts TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  input TEXT,
  tools_called TEXT,
  output TEXT,
  model TEXT,
  input_tokens INTEGER,
  output_tokens INTEGER
);

CREATE TRIGGER core_areas_no_delete BEFORE DELETE ON core_areas
BEGIN SELECT RAISE(ABORT, 'rows are never deleted; archive them instead'); END;
CREATE TRIGGER core_tasks_no_delete BEFORE DELETE ON core_tasks
BEGIN SELECT RAISE(ABORT, 'rows are never deleted; archive them instead'); END;
CREATE TRIGGER core_schedule_blocks_no_delete BEFORE DELETE ON core_schedule_blocks
BEGIN SELECT RAISE(ABORT, 'rows are never deleted; archive them instead'); END;
CREATE TRIGGER core_agent_log_no_delete BEFORE DELETE ON core_agent_log
BEGIN SELECT RAISE(ABORT, 'rows are never deleted; archive them instead'); END;

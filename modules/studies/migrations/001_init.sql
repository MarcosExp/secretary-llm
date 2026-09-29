-- Studies: programs, courses, study units and the link between courses and core tasks.

CREATE TABLE studies_programs (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE COLLATE NOCASE,
  status TEXT NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'archived'))
);

CREATE TABLE studies_courses (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  program_id INTEGER REFERENCES studies_programs(id),
  status TEXT NOT NULL DEFAULT 'enrolled' CHECK (status IN ('enrolled', 'passed', 'archived')),
  exam_date DATE CHECK (exam_date IS NULL OR date(exam_date) IS exam_date)
);

CREATE TABLE studies_units (
  id INTEGER PRIMARY KEY,
  course_id INTEGER NOT NULL REFERENCES studies_courses(id),
  unit TEXT NOT NULL,
  start DATE,
  "end" DATE,
  estimate_h REAL CHECK (estimate_h IS NULL OR estimate_h > 0),
  done_h REAL NOT NULL DEFAULT 0 CHECK (done_h >= 0),
  confidence INTEGER CHECK (confidence IS NULL OR confidence BETWEEN 1 AND 5),
  status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'in_progress', 'done', 'archived'))
);

-- Cross-module link: the dependent module owns the reference to core.
CREATE TABLE studies_course_tasks (
  course_id INTEGER NOT NULL REFERENCES studies_courses(id),
  task_id INTEGER NOT NULL REFERENCES core_tasks(id),
  PRIMARY KEY (course_id, task_id)
);

CREATE TRIGGER studies_programs_no_delete BEFORE DELETE ON studies_programs
BEGIN SELECT RAISE(ABORT, 'rows are never deleted; archive them instead'); END;
CREATE TRIGGER studies_courses_no_delete BEFORE DELETE ON studies_courses
BEGIN SELECT RAISE(ABORT, 'rows are never deleted; archive them instead'); END;
CREATE TRIGGER studies_units_no_delete BEFORE DELETE ON studies_units
BEGIN SELECT RAISE(ABORT, 'rows are never deleted; archive them instead'); END;
CREATE TRIGGER studies_course_tasks_no_delete BEFORE DELETE ON studies_course_tasks
BEGIN SELECT RAISE(ABORT, 'rows are never deleted; archive them instead'); END;

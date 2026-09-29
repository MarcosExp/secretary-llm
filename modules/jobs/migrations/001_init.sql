-- Job search: applications and the tasks linked to them.

CREATE TABLE jobs_applications (
  id INTEGER PRIMARY KEY,
  company TEXT NOT NULL CHECK (length(trim(company)) > 0),
  role TEXT,                  -- e.g. SWE, ML/AI, Data
  program TEXT,               -- e.g. a named internship program
  stage TEXT NOT NULL DEFAULT 'researching' CHECK (stage IN (
    'researching', 'to_apply', 'applied', 'assessment', 'interview', 'final',
    'offer', 'rejected', 'no_response', 'archived')),
  priority TEXT CHECK (priority IS NULL OR priority IN ('dream', 'strong', 'backup')),
  location TEXT,
  visa TEXT CHECK (visa IS NULL OR visa IN ('sponsors', 'eu_only', 'unclear', 'no_sponsorship')),
  referral INTEGER NOT NULL DEFAULT 0 CHECK (referral IN (0, 1)),
  process_open INTEGER CHECK (process_open IS NULL OR process_open IN (0, 1)),
  link TEXT,
  contact TEXT,
  applied_on DATE CHECK (applied_on IS NULL OR date(applied_on) IS applied_on),
  deadline DATE CHECK (deadline IS NULL OR date(deadline) IS deadline),
  follow_up_by DATE CHECK (follow_up_by IS NULL OR date(follow_up_by) IS follow_up_by),
  next_step TEXT,
  notes TEXT,
  external_ref TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE UNIQUE INDEX jobs_applications_external_ref ON jobs_applications (external_ref) WHERE external_ref IS NOT NULL;

CREATE TRIGGER jobs_applications_touch AFTER UPDATE ON jobs_applications
FOR EACH ROW WHEN NEW.updated_at IS OLD.updated_at
BEGIN
  UPDATE jobs_applications SET updated_at = CURRENT_TIMESTAMP WHERE id = NEW.id;
END;

CREATE TABLE jobs_application_tasks (
  application_id INTEGER NOT NULL REFERENCES jobs_applications(id),
  task_id INTEGER NOT NULL REFERENCES core_tasks(id),
  PRIMARY KEY (application_id, task_id)
);

CREATE TRIGGER jobs_applications_no_delete BEFORE DELETE ON jobs_applications
BEGIN SELECT RAISE(ABORT, 'rows are never deleted; archive them instead'); END;
CREATE TRIGGER jobs_application_tasks_no_delete BEFORE DELETE ON jobs_application_tasks
BEGIN SELECT RAISE(ABORT, 'rows are never deleted; archive them instead'); END;

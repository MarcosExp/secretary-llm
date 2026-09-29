-- Course and unit details commonly tracked in a study planner, plus the source of
-- imported rows (e.g. "notion:<page id>").

ALTER TABLE studies_courses ADD COLUMN ects REAL;
ALTER TABLE studies_courses ADD COLUMN term TEXT;
ALTER TABLE studies_courses ADD COLUMN grade REAL;
ALTER TABLE studies_courses ADD COLUMN notes TEXT;
ALTER TABLE studies_courses ADD COLUMN external_ref TEXT;
CREATE UNIQUE INDEX studies_courses_external_ref ON studies_courses (external_ref) WHERE external_ref IS NOT NULL;

ALTER TABLE studies_units ADD COLUMN position INTEGER;   -- order within the course
ALTER TABLE studies_units ADD COLUMN weight TEXT;        -- how much it counts in the exam
ALTER TABLE studies_units ADD COLUMN notes TEXT;
ALTER TABLE studies_units ADD COLUMN external_ref TEXT;
CREATE UNIQUE INDEX studies_units_external_ref ON studies_units (external_ref) WHERE external_ref IS NOT NULL;

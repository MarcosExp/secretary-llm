-- Fictional example data for demos and screenshots. Dates are relative to today.

INSERT INTO studies_programs (name) VALUES ('BSc Computer Science');

INSERT INTO studies_courses (name, program_id, exam_date) VALUES
  ('Linear Algebra', (SELECT id FROM studies_programs WHERE name = 'BSc Computer Science'), date('now', '+40 days')),
  ('Algorithms and Data Structures', (SELECT id FROM studies_programs WHERE name = 'BSc Computer Science'), date('now', '+55 days'));

INSERT INTO studies_units (course_id, unit, start, "end", estimate_h, done_h, confidence, status) VALUES
  ((SELECT id FROM studies_courses WHERE name = 'Linear Algebra'), 'Vector spaces', date('now', '-14 days'), date('now', '-7 days'), 8, 8, 4, 'done'),
  ((SELECT id FROM studies_courses WHERE name = 'Linear Algebra'), 'Linear maps', date('now', '-7 days'), date('now', '+7 days'), 10, 4, 2, 'in_progress'),
  ((SELECT id FROM studies_courses WHERE name = 'Linear Algebra'), 'Eigenvalues', date('now', '+7 days'), date('now', '+21 days'), 12, 0, NULL, 'pending'),
  ((SELECT id FROM studies_courses WHERE name = 'Algorithms and Data Structures'), 'Graphs', date('now', '-3 days'), date('now', '+11 days'), 10, 2, 3, 'in_progress');

INSERT INTO studies_course_tasks (course_id, task_id) VALUES
  ((SELECT id FROM studies_courses WHERE name = 'Linear Algebra'),
   (SELECT id FROM core_tasks WHERE title = 'Submit Linear Algebra assignment 2')),
  ((SELECT id FROM studies_courses WHERE name = 'Algorithms and Data Structures'),
   (SELECT id FROM core_tasks WHERE title = 'Solve two graph problems'));

-- Fictional example data for demos and screenshots. Dates are relative to today.

INSERT INTO core_areas (name) VALUES ('Work'), ('Studies'), ('Projects'), ('Health'), ('Personal');

INSERT INTO core_tasks (title, priority, area_id, due, estimate_h, notes) VALUES
  ('Submit Linear Algebra assignment 2', 'P1', (SELECT id FROM core_areas WHERE name = 'Studies'), date('now', '+2 days'), 3, 'Exercises 4-9'),
  ('Review open pull requests', 'P1', (SELECT id FROM core_areas WHERE name = 'Work'), date('now'), 1, NULL),
  ('Solve two graph problems', 'P2', (SELECT id FROM core_areas WHERE name = 'Studies'), date('now', '+5 days'), 1.5, 'BFS and topological sort'),
  ('Write the project README', 'P2', (SELECT id FROM core_areas WHERE name = 'Projects'), date('now', '+6 days'), 2, NULL),
  ('Plan next week''s training', 'P2', (SELECT id FROM core_areas WHERE name = 'Health'), NULL, 0.5, NULL),
  ('Renew passport', 'P3', (SELECT id FROM core_areas WHERE name = 'Personal'), NULL, NULL, 'Check the appointment website');

INSERT INTO core_schedule_blocks (name, kind, weekday, start_time, end_time) VALUES
  ('Morning algorithms practice', 'practice', 0, '07:30', '08:30'),
  ('Morning algorithms practice', 'practice', 2, '07:30', '08:30'),
  ('Running club', 'training', 3, '19:00', '20:00');

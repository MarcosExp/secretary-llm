import sqlite3

import pytest

from app.db.retention import purge_archived, statements

OLD = "2000-01-01 00:00:00"  # archived long ago


def one(conn, sql, *params):
    return conn.execute(sql, params).fetchone()


def add_task(conn, title, status="todo", area_id=None):
    return conn.execute("INSERT INTO core_tasks (title, status, area_id) VALUES (?, ?, ?)",
                        (title, status, area_id)).lastrowid


def age(conn, table, row_id):
    conn.execute(f"UPDATE {table} SET archived_at = ? WHERE id = ?", (OLD, row_id))


def titles(conn):
    return {row[0] for row in conn.execute("SELECT title FROM core_tasks")}


def test_archived_at_follows_the_status(conn):
    task = add_task(conn, "a")
    assert one(conn, "SELECT archived_at FROM core_tasks WHERE id = ?", task)[0] is None
    conn.execute("UPDATE core_tasks SET status = 'archived' WHERE id = ?", (task,))
    assert one(conn, "SELECT archived_at FROM core_tasks WHERE id = ?", task)[0] is not None
    conn.execute("UPDATE core_tasks SET status = 'todo' WHERE id = ?", (task,))  # reopened: clock stops
    assert one(conn, "SELECT archived_at FROM core_tasks WHERE id = ?", task)[0] is None
    inserted = add_task(conn, "b", status="archived")  # e.g. imported: stamped by the next purge
    assert one(conn, "SELECT archived_at FROM core_tasks WHERE id = ?", inserted)[0] is None
    conn.commit()
    purge_archived(conn)
    assert one(conn, "SELECT archived_at FROM core_tasks WHERE id = ?", inserted)[0] is not None


def test_only_rows_archived_for_30_days_are_deleted(conn):
    old = add_task(conn, "old", "archived")
    add_task(conn, "recent", "archived")
    add_task(conn, "done long ago", "done")
    reopened = add_task(conn, "reopened", "archived")
    for task in (old, reopened):
        age(conn, "core_tasks", task)
    conn.execute("UPDATE core_tasks SET status = 'doing' WHERE id = ?", (reopened,))
    conn.execute("UPDATE core_tasks SET status = 'archived' WHERE id = ?", (reopened,))  # clock restarts
    conn.commit()

    assert purge_archived(conn) == {"core_tasks": 1}
    assert titles(conn) == {"recent", "done long ago", "reopened"}
    assert purge_archived(conn) == {}


def test_the_database_refuses_any_other_delete(conn):
    task = add_task(conn, "recent", "archived")
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        conn.execute("DELETE FROM core_tasks WHERE id = ?", (task,))
    conn.execute("UPDATE core_tasks SET archived_at = NULL WHERE id = ?", (task,))
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        conn.execute("DELETE FROM core_tasks WHERE id = ?", (task,))


def test_links_units_and_containers(conn):
    area = conn.execute("INSERT INTO core_areas (name, status) VALUES ('Old area', 'archived')").lastrowid
    empty_area = conn.execute("INSERT INTO core_areas (name, status) VALUES ('Unused', 'archived')").lastrowid
    program = conn.execute("INSERT INTO studies_programs (name, status) VALUES ('BSc', 'archived')").lastrowid
    course = conn.execute("INSERT INTO studies_courses (name, status, program_id) VALUES ('Algebra', 'archived', ?)",
                          (program,)).lastrowid
    live_course = conn.execute("INSERT INTO studies_courses (name, program_id) VALUES ('Physics', ?)",
                               (program,)).lastrowid
    conn.execute("INSERT INTO studies_units (course_id, unit) VALUES (?, 'Matrices')", (course,))
    conn.execute("INSERT INTO studies_units (course_id, unit) VALUES (?, 'Optics')", (live_course,))
    app = conn.execute("INSERT INTO jobs_applications (company, stage) VALUES ('Acme', 'archived')").lastrowid

    kept = add_task(conn, "kept, uses the area", area_id=area)
    purged = add_task(conn, "linked and archived", "archived")
    course_task = add_task(conn, "linked to the old course")
    conn.execute("INSERT INTO studies_course_tasks VALUES (?, ?)", (live_course, purged))
    conn.execute("INSERT INTO studies_course_tasks VALUES (?, ?)", (course, course_task))
    conn.execute("INSERT INTO jobs_application_tasks VALUES (?, ?)", (app, kept))
    for table, row in [("core_areas", area), ("core_areas", empty_area), ("studies_programs", program),
                       ("studies_courses", course), ("jobs_applications", app), ("core_tasks", purged)]:
        age(conn, table, row)
    conn.commit()

    deleted = purge_archived(conn)
    assert deleted == {
        "jobs_application_tasks": 1, "jobs_applications": 1,
        "studies_course_tasks": 2, "studies_units": 1, "studies_courses": 1,
        "core_tasks": 1, "core_areas": 1,
    }
    assert titles(conn) == {"kept, uses the area", "linked to the old course"}
    assert one(conn, "SELECT name FROM core_areas")[0] == "Old area"  # a task still uses it
    assert one(conn, "SELECT name FROM studies_programs")[0] == "BSc"  # Physics still uses it
    assert [r[0] for r in conn.execute("SELECT unit FROM studies_units")] == ["Optics"]


def test_a_failing_purge_deletes_nothing(conn, tmp_path):
    task = add_task(conn, "old", "archived")
    age(conn, "core_tasks", task)
    conn.execute("INSERT INTO core_areas (name) VALUES ('live')")
    conn.commit()
    for module, sql in {"aa": "DELETE FROM core_tasks WHERE status = 'archived';",  # runs first, allowed
                        "core": "DELETE FROM core_areas;"}.items():  # refused by the trigger
        (tmp_path / module).mkdir()
        (tmp_path / module / "purge.sql").write_text(sql)
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        purge_archived(conn, tmp_path)
    assert titles(conn) == {"old"}


def test_statement_splitting():
    sql = "-- comment; with a semicolon\nSELECT 1;\n\nSELECT 'a;b'\nFROM x;\n"
    assert statements(sql) == ["SELECT 1;", "SELECT 'a;b'\nFROM x;"]
    with pytest.raises(ValueError, match="incomplete"):
        statements("SELECT 1")

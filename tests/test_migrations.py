import sqlite3

import pytest

from app.db import connect
from app.db.migrations import MigrationError, discover, migrate


def make_module(root, name, files):
    migrations = root / name / "migrations"
    migrations.mkdir(parents=True)
    for file_name, sql in files.items():
        (migrations / file_name).write_text(sql, encoding="utf-8")


def tables(conn):
    return {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}


def test_real_migrations_apply_and_are_idempotent(tmp_path):
    conn = connect(tmp_path / "db.sqlite")
    applied = migrate(conn)
    order = [(m.module, m.version) for m in applied]
    assert order[0] == ("core", 1) and order == sorted(order, key=lambda m: (m[0] != "core", m))
    assert {("core", 2), ("studies", 1)} <= set(order)
    assert {"core_tasks", "core_areas", "studies_courses", "studies_course_tasks"} <= tables(conn)
    assert migrate(conn) == []


def test_core_runs_first_then_modules_alphabetically(tmp_path):
    make_module(tmp_path, "alpha", {"001_a.sql": "CREATE TABLE alpha_x (id INTEGER);"})
    make_module(tmp_path, "core", {"001_a.sql": "CREATE TABLE core_x (id INTEGER);",
                                   "002_b.sql": "CREATE TABLE core_y (id INTEGER);"})
    order = [(m.module, m.version) for m in discover(tmp_path)]
    assert order == [("core", 1), ("core", 2), ("alpha", 1)]


def test_editing_an_applied_migration_is_rejected(tmp_path):
    make_module(tmp_path, "core", {"001_a.sql": "CREATE TABLE core_x (id INTEGER);"})
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn, tmp_path)
    (tmp_path / "core" / "migrations" / "001_a.sql").write_text(
        "CREATE TABLE core_x (id INTEGER, extra TEXT);", encoding="utf-8"
    )
    with pytest.raises(MigrationError, match="changed after being applied"):
        migrate(conn, tmp_path)


def test_line_endings_do_not_change_the_checksum(tmp_path):
    make_module(tmp_path, "core", {"001_a.sql": "CREATE TABLE core_x (id INTEGER);\n"})
    conn = connect(tmp_path / "db.sqlite")
    migrate(conn, tmp_path)
    (tmp_path / "core" / "migrations" / "001_a.sql").write_bytes(b"CREATE TABLE core_x (id INTEGER);\r\n")
    assert migrate(conn, tmp_path) == []


def test_failed_migration_rolls_back_completely(tmp_path):
    make_module(tmp_path, "core", {
        "001_a.sql": "CREATE TABLE core_x (id INTEGER);",
        "002_b.sql": "CREATE TABLE core_y (id INTEGER); INSERT INTO missing_table VALUES (1);",
    })
    conn = connect(tmp_path / "db.sqlite")
    with pytest.raises(MigrationError, match="002_b.sql failed"):
        migrate(conn, tmp_path)
    assert "core_x" in tables(conn)
    assert "core_y" not in tables(conn)  # the half-applied file left nothing behind
    versions = [row[0] for row in conn.execute("SELECT version FROM schema_migrations")]
    assert versions == [1]


def test_invalid_file_name_is_rejected(tmp_path):
    make_module(tmp_path, "core", {"1_bad name.sql": "SELECT 1;"})
    with pytest.raises(MigrationError, match="invalid migration file name"):
        discover(tmp_path)


@pytest.mark.parametrize("start, end, ok", [
    ("07:30", "08:30", True),
    ("07:30:00", "08:30:00", False),  # HH:MM only
    ("25:00", "26:00", False),
    ("09:00", "08:00", False),        # ends before it starts
])
def test_schedule_block_times(conn, start, end, ok):
    insert = ("INSERT INTO core_schedule_blocks (name, kind, weekday, start_time, end_time) "
              "VALUES ('b', 'k', 0, ?, ?)")
    if ok:
        conn.execute(insert, (start, end))
    else:
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(insert, (start, end))


def test_rows_can_never_be_deleted(conn):
    conn.execute("INSERT INTO core_areas (name) VALUES ('Work')")
    with pytest.raises(sqlite3.IntegrityError, match="never deleted"):
        conn.execute("DELETE FROM core_areas")

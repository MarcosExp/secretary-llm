import sqlite3

import pytest

from app.db import connect
from app.db.migrations import migrate
from app.sdk.access import Guard
from app.sdk.errors import AccessDenied

NO_READS = frozenset()


@pytest.fixture
def guard(tmp_path):
    conn = connect(tmp_path / "db.sqlite", cached_statements=0)
    migrate(conn)
    conn.execute("INSERT INTO core_tasks (title) VALUES ('t')")
    conn.commit()
    yield Guard(conn)
    conn.close()


def test_module_reads_and_writes_its_own_tables(guard):
    guard.execute("core", NO_READS, "UPDATE core_tasks SET title = 'u'")
    assert guard.execute("core", NO_READS, "SELECT title FROM core_tasks").fetchone()[0] == "u"


def test_reading_another_module_needs_a_declared_read(guard):
    with pytest.raises(AccessDenied, match="may not read core_tasks"):
        guard.execute("studies", NO_READS, "SELECT title FROM core_tasks")
    rows = guard.execute("studies", frozenset({"core_tasks"}), "SELECT title FROM core_tasks")
    assert rows.fetchall()


def test_joins_are_checked_table_by_table(guard):
    with pytest.raises(AccessDenied, match="core_areas"):
        guard.execute(
            "studies", frozenset({"core_tasks"}),
            "SELECT t.title FROM core_tasks t LEFT JOIN core_areas a ON a.id = t.area_id",
        )


@pytest.mark.parametrize("sql", [
    "UPDATE core_tasks SET title = 'hijacked'",
    "INSERT INTO core_tasks (title) VALUES ('sneaky')",
])
def test_writes_to_other_modules_are_denied_even_when_readable(guard, sql):
    with pytest.raises(AccessDenied, match="call the owning module's tool"):
        guard.execute("studies", frozenset({"core_tasks"}), sql)


@pytest.mark.parametrize("sql", [
    "CREATE TABLE studies_extra (id INTEGER)",
    "DROP TABLE studies_units",
    "ALTER TABLE studies_units ADD COLUMN x TEXT",
    "PRAGMA foreign_keys = OFF",
    "ATTACH DATABASE ':memory:' AS other",
])
def test_schema_changes_and_pragmas_are_denied(guard, sql):
    with pytest.raises(AccessDenied, match="schema or pragma"):
        guard.execute("studies", NO_READS, sql)


def test_triggers_from_migrations_still_run(guard):
    # core_tasks_touch updates core_tasks from inside a trigger.
    before = guard.conn.execute("SELECT updated_at FROM core_tasks").fetchone()[0]
    guard.conn.execute("UPDATE core_tasks SET updated_at = '2000-01-01 00:00:00'")
    guard.execute("core", NO_READS, "UPDATE core_tasks SET title = 'v'")
    assert guard.conn.execute("SELECT updated_at FROM core_tasks").fetchone()[0] >= before


def test_modules_cannot_delete_even_their_own_rows(guard):
    with pytest.raises(AccessDenied, match="never deleted"):
        guard.execute("core", NO_READS, "DELETE FROM core_tasks")


def test_system_code_is_unrestricted(guard):
    guard.conn.execute("SELECT * FROM studies_units JOIN core_tasks")


def test_statement_cache_must_be_off(tmp_path):
    """Why the agent connection uses cached_statements=0.

    SQLite authorizes a statement when it is prepared. With the cache on, a statement
    prepared by one module is reused by the next one without being checked.
    """
    conn = connect(tmp_path / "db.sqlite")  # default cache
    migrate(conn)
    guard = Guard(conn)
    sql = "SELECT title FROM core_tasks"
    guard.execute("core", NO_READS, sql)
    guard.execute("jobs", NO_READS, sql)  # not denied: reused from the cache

    safe = Guard(connect(tmp_path / "db.sqlite", cached_statements=0))
    safe.execute("core", NO_READS, sql)
    with pytest.raises(AccessDenied):
        safe.execute("jobs", NO_READS, sql)

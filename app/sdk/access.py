"""Per-module database access, enforced by SQLite itself.

A module owns the tables prefixed with its name. Through ModuleContext.db it can:
- read and write its own tables,
- read the tables listed under `reads` in its manifest (including tables its foreign
  keys point to: SQLite reads them to check the keys),
- nothing else: no deletes (rows are archived), no writes to other modules' tables,
  no schema changes, no pragmas.

The rules live in an SQLite authorizer callback, so they hold for any SQL a module
writes. SQLite authorizes a statement when it is prepared, so the connection must be
opened with cached_statements=0: a statement cached while one module was active
would otherwise be reused by another without being checked again.
"""

import sqlite3
from contextlib import contextmanager

from app.sdk.errors import AccessDenied

SQLITE_RECURSIVE = getattr(sqlite3, "SQLITE_RECURSIVE", 33)
ALWAYS_ALLOWED = {
    sqlite3.SQLITE_SELECT,
    sqlite3.SQLITE_FUNCTION,
    sqlite3.SQLITE_TRANSACTION,
    sqlite3.SQLITE_SAVEPOINT,
    SQLITE_RECURSIVE,
}
SCHEMA_CHANGE = "module '{module}' may not run this statement (schema or pragma change)"
WRITES = {
    sqlite3.SQLITE_INSERT: "insert into",
    sqlite3.SQLITE_UPDATE: "update",
}


class Guard:
    """Installs the authorizer on a shared connection and tracks which module is acting.

    With no module acting (migrations, agent logging), everything is allowed.
    """

    def __init__(self, conn: sqlite3.Connection):
        self.conn = conn
        self._acting: list[tuple[str, frozenset[str]]] = []
        self._denied: str | None = None
        conn.set_authorizer(self._authorize)

    @contextmanager
    def acting_as(self, module: str, reads: frozenset[str]):
        self._acting.append((module, reads))
        try:
            yield
        finally:
            self._acting.pop()

    def execute(self, module: str, reads: frozenset[str], sql: str, params=()) -> sqlite3.Cursor:
        self._denied = None
        with self.acting_as(module, reads):
            try:
                return self.conn.execute(sql, params)
            except sqlite3.DatabaseError as exc:
                if self._denied:
                    raise AccessDenied(self._denied) from exc
                raise

    def _authorize(self, action, arg1, arg2, db_name, source):
        if not self._acting or source is not None:
            # System code, or statements inside a trigger/view defined by a reviewed migration.
            return sqlite3.SQLITE_OK
        module, reads = self._acting[-1]
        if action in ALWAYS_ALLOWED:
            return sqlite3.SQLITE_OK
        if isinstance(arg1, str) and arg1.startswith("sqlite_"):
            # CREATE/DROP/ALTER show up first as writes to SQLite's internal schema tables.
            return self._deny(SCHEMA_CHANGE.format(module=module))
        if action == sqlite3.SQLITE_DELETE:
            return self._deny("rows are never deleted; archive them instead")
        if action == sqlite3.SQLITE_READ:
            table = arg1
            if table.startswith(f"{module}_") or table in reads:
                return sqlite3.SQLITE_OK
            return self._deny(f"module '{module}' may not read {table} (not declared in reads)")
        if action in WRITES:
            table = arg1
            if table.startswith(f"{module}_"):
                return sqlite3.SQLITE_OK
            return self._deny(
                f"module '{module}' may not {WRITES[action]} {table}; "
                "call the owning module's tool instead"
            )
        return self._deny(SCHEMA_CHANGE.format(module=module))

    def _deny(self, reason: str) -> int:
        # Keep the first reason: later denials in the same statement are side effects
        # (e.g. the foreign-key reads SQLite adds to a DELETE).
        self._denied = self._denied or reason
        return sqlite3.SQLITE_DENY


class ScopedDB:
    """What a module sees as ctx.db: the shared connection, acting as that module."""

    def __init__(self, guard: Guard, module: str, reads: frozenset[str]):
        self._guard = guard
        self.module = module
        self.reads = reads

    def execute(self, sql: str, params=()) -> sqlite3.Cursor:
        return self._guard.execute(self.module, self.reads, sql, params)

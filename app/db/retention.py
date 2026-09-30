"""Deletes rows that have stayed archived for 30 days.

Each module that owns archivable rows ships `purge.sql`. The other modules run first,
so they remove their links to core rows before core deletes them. Everything runs in
one transaction: if any statement fails, nothing is deleted. The no_delete triggers
refuse to delete anything else, whatever a purge file says.
"""

import re
import sqlite3
from pathlib import Path

from app.db.migrations import CORE_MODULE, MODULES_DIR

DELETE = re.compile(r"^\s*DELETE\s+FROM\s+(\w+)", re.I | re.M)


def statements(sql: str) -> list[str]:
    found, current = [], ""
    for line in sql.splitlines(keepends=True):
        if line.lstrip().startswith("--"):
            continue
        current += line
        if sqlite3.complete_statement(current):
            found.append(current.strip())
            current = ""
    if current.strip():
        raise ValueError(f"incomplete SQL statement: {current.strip()[:60]}")
    return found


def purge_archived(conn: sqlite3.Connection, modules_dir: Path = MODULES_DIR) -> dict[str, int]:
    """Delete expired archived rows. Returns rows deleted per table (tables with none left out)."""
    files = sorted(modules_dir.glob("*/purge.sql"), key=lambda p: (p.parent.name == CORE_MODULE, p.parent.name))
    deleted: dict[str, int] = {}
    with conn:
        for file in files:
            for statement in statements(file.read_text(encoding="utf-8")):
                count = conn.execute(statement).rowcount
                if (match := DELETE.match(statement)) and count > 0:
                    deleted[match.group(1)] = deleted.get(match.group(1), 0) + count
    return deleted

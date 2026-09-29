"""Loads each module's fictional example data (modules/<module>/seed_example.sql)."""

import sqlite3
from pathlib import Path

from app.db.migrations import CORE_MODULE, MODULES_DIR

SEED_FILE = "seed_example.sql"


class SeedError(Exception):
    pass


def seed_example(conn: sqlite3.Connection, modules_dir: Path = MODULES_DIR) -> list[str]:
    """Load example data into an empty database, all modules in one transaction.

    Refuses to run if there are tasks already, so it can never mix fictional
    rows into real data. Returns the modules seeded.
    """
    if conn.execute("SELECT 1 FROM core_tasks LIMIT 1").fetchone():
        raise SeedError("the database already has tasks; example data only goes into an empty one")
    files = sorted(
        modules_dir.glob(f"*/{SEED_FILE}"),
        key=lambda f: (f.parent.name != CORE_MODULE, f.parent.name),
    )
    script = "\n".join(f.read_text(encoding="utf-8") for f in files)
    try:
        conn.executescript(f"BEGIN;\n{script}\nCOMMIT;")
    except sqlite3.Error as exc:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise SeedError(f"example data failed to load: {exc}") from exc
    return [f.parent.name for f in files]

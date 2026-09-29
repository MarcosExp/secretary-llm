"""SQLite connection settings shared by the whole app."""

import os
import sqlite3
from pathlib import Path


def db_path() -> Path:
    return Path(os.getenv("SECRETARY_DB_PATH", "/data/secretary.db"))


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open a connection configured for Litestream: WAL mode and a busy timeout.

    Litestream requires WAL and takes brief locks while it copies the WAL,
    so writers must wait instead of failing immediately.
    """
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

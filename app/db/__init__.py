"""SQLite connection settings shared by the whole app."""

import os
import sqlite3
from pathlib import Path


def db_path() -> Path:
    """SECRETARY_DB_PATH, else <SECRETARY_DATA_DIR>/db/secretary.db, else the container path."""
    if path := os.getenv("SECRETARY_DB_PATH"):
        return Path(path)
    if data_dir := os.getenv("SECRETARY_DATA_DIR"):
        return Path(data_dir) / "db" / "secretary.db"
    return Path("/data/secretary.db")


def connect(path: Path | None = None, *, cached_statements: int = 128,
            check_same_thread: bool = True) -> sqlite3.Connection:
    """Open a connection configured for Litestream: WAL mode and a busy timeout.

    Litestream requires WAL and takes brief locks while it copies the WAL,
    so writers must wait instead of failing immediately. Connections that enforce
    module permissions pass cached_statements=0 (see app/sdk/access.py).
    """
    path = path or db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, cached_statements=cached_statements,
                           check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn

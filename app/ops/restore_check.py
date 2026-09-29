"""Backup round-trip check.

    python -m app.ops.restore_check write                 -> writes a probe row, prints its token
    python -m app.ops.restore_check verify <db> <token>   -> checks a restored copy

The verify step fails if the restored copy is corrupt, lacks the probe row,
or is missing any table that exists in the live database.
"""

import sqlite3
import sys
import uuid
from pathlib import Path

from app.db import connect, db_path

PROBE_TABLE = "_backup_probe"


def write(db: Path | None = None) -> str:
    token = uuid.uuid4().hex
    conn = connect(db)
    try:
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {PROBE_TABLE} "
            "(token TEXT PRIMARY KEY, ts TIMESTAMP DEFAULT CURRENT_TIMESTAMP)"
        )
        conn.execute(f"INSERT INTO {PROBE_TABLE} (token) VALUES (?)", (token,))
        conn.commit()
    finally:
        conn.close()
    return token


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = [
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    return {name: conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0] for name in names}


def verify(restored: Path, token: str, live_db: Path | None = None) -> bool:
    backup = sqlite3.connect(f"file:{restored}?mode=ro", uri=True)
    live = sqlite3.connect(f"file:{live_db or db_path()}?mode=ro", uri=True)
    try:
        integrity = backup.execute("PRAGMA integrity_check").fetchone()[0]
        live_counts, backup_counts = table_counts(live), table_counts(backup)
        has_probe = PROBE_TABLE in backup_counts and backup.execute(
            f"SELECT 1 FROM {PROBE_TABLE} WHERE token = ?", (token,)
        ).fetchone() is not None
    finally:
        backup.close()
        live.close()

    missing = sorted(set(live_counts) - set(backup_counts))
    print(f"integrity_check: {integrity}")
    print(f"probe row:       {'found' if has_probe else 'MISSING'}")
    print(f"{'table':<30}{'live':>10}{'restored':>10}")
    for name in sorted(live_counts):
        print(f"{name:<30}{live_counts[name]:>10}{backup_counts.get(name, '-'):>10}")
    if missing:
        print(f"missing tables: {', '.join(missing)}")
    return integrity == "ok" and has_probe and not missing


def main(argv: list[str]) -> int:
    if argv[:1] == ["write"]:
        print(write())
        return 0
    if argv[:1] == ["verify"] and len(argv) == 3:
        return 0 if verify(Path(argv[1]), argv[2]) else 1
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

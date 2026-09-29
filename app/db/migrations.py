"""Versioned SQL migrations, one folder per module.

    modules/<module>/migrations/NNN_description.sql

Each file runs in a single transaction and is recorded in schema_migrations with
a checksum. Editing a migration after it has been applied is an error: schema
changes always go in a new file. Migration files must not contain BEGIN/COMMIT.
"""

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import modules

MODULES_DIR = Path(modules.__file__).parent
CORE_MODULE = "core"
MODULE_NAME = re.compile(r"^[a-z][a-z0-9_]*$")
FILE_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")


class MigrationError(Exception):
    pass


@dataclass(frozen=True)
class Migration:
    module: str
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def discover(modules_dir: Path = MODULES_DIR) -> list[Migration]:
    """All migrations on disk, core first (other modules may reference its tables)."""
    found = []
    for module_dir in sorted(p for p in modules_dir.iterdir() if (p / "migrations").is_dir()):
        module = module_dir.name
        if not MODULE_NAME.match(module):
            raise MigrationError(f"invalid module name: {module}")
        seen = set()
        for file in sorted((module_dir / "migrations").glob("*.sql")):
            match = FILE_NAME.match(file.name)
            if not match:
                raise MigrationError(f"invalid migration file name: {module}/{file.name}")
            version = int(match.group(1))
            if version in seen:
                raise MigrationError(f"duplicate version {version:03d} in module {module}")
            seen.add(version)
            # Normalize line endings so a Windows checkout has the same checksum.
            sql = file.read_text(encoding="utf-8").replace("\r\n", "\n")
            found.append(Migration(module, version, file.name, sql))
    return sorted(found, key=lambda m: (m.module != CORE_MODULE, m.module, m.version))


def migrate(conn: sqlite3.Connection, modules_dir: Path = MODULES_DIR) -> list[Migration]:
    """Apply pending migrations in order. Returns the ones applied."""
    conn.execute(
        """CREATE TABLE IF NOT EXISTS schema_migrations (
             module TEXT NOT NULL,
             version INTEGER NOT NULL,
             name TEXT NOT NULL,
             checksum TEXT NOT NULL,
             applied_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
             PRIMARY KEY (module, version)
           )"""
    )
    conn.commit()
    applied = {
        (row[0], row[1]): row[2]
        for row in conn.execute("SELECT module, version, checksum FROM schema_migrations")
    }

    pending = []
    for migration in discover(modules_dir):
        checksum = applied.get((migration.module, migration.version))
        if checksum is None:
            pending.append(migration)
        elif checksum != migration.checksum:
            raise MigrationError(
                f"{migration.module}/{migration.name} changed after being applied; "
                "add a new migration instead"
            )

    for migration in pending:
        _apply(conn, migration)
    return pending


def _apply(conn: sqlite3.Connection, migration: Migration) -> None:
    # Module and file names are validated by discover(), so inlining them is safe.
    record = (
        "INSERT INTO schema_migrations (module, version, name, checksum) VALUES "
        f"('{migration.module}', {migration.version}, '{migration.name}', '{migration.checksum}');"
    )
    try:
        conn.executescript(f"BEGIN;\n{migration.sql}\n;\n{record}\nCOMMIT;")
    except sqlite3.Error as exc:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise MigrationError(f"{migration.module}/{migration.name} failed: {exc}") from exc

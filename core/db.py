"""Database connection and migration runner for open_otto.

Migrations are plain .sql files named NNN_name.sql (for example 001_core.sql).
They run in version order. Each one runs inside a single transaction together
with its schema_migrations bookkeeping row, so a failure leaves nothing behind.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

MIGRATION_FILE = re.compile(r"^(\d{3,})_([a-z0-9_]+)\.sql$")

DEFAULT_MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / "migrations"
DEFAULT_SEEDS_DIR = Path(__file__).resolve().parent.parent / "seeds"


class MigrationError(Exception):
    """Raised when migrations are malformed, out of order, or fail to apply."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    path: Path


def connect(path: str | Path = ":memory:") -> sqlite3.Connection:
    """Open a connection with foreign keys on and dict-style rows."""
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def discover_migrations(directory: str | Path = DEFAULT_MIGRATIONS_DIR) -> list[Migration]:
    """Return migrations in version order. Reject bad names and duplicate versions."""
    found: dict[int, Migration] = {}
    for path in sorted(Path(directory).glob("*.sql")):
        match = MIGRATION_FILE.match(path.name)
        if not match:
            raise MigrationError(f"bad migration filename: {path.name} (want NNN_name.sql)")
        version = int(match.group(1))
        if version in found:
            raise MigrationError(
                f"duplicate migration version {version}: {found[version].path.name} and {path.name}"
            )
        found[version] = Migration(version, match.group(2), path)
    return [found[v] for v in sorted(found)]


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    """Versions already applied. Empty set if the database has no migration table yet."""
    has_table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if not has_table:
        return set()
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}


def _run_script(conn: sqlite3.Connection, sql: str) -> None:
    """Run a script atomically. SQLite DDL is transactional, so this is all-or-nothing."""
    try:
        conn.executescript(f"BEGIN;\n{sql}\nCOMMIT;")
    except sqlite3.Error:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def migrate(
    conn: sqlite3.Connection,
    directory: str | Path = DEFAULT_MIGRATIONS_DIR,
) -> list[int]:
    """Apply all pending migrations in order. Returns the versions applied this call."""
    migrations = discover_migrations(directory)
    known = {m.version for m in migrations}
    done = applied_versions(conn)

    missing_files = done - known
    if missing_files:
        raise MigrationError(
            f"database has migrations with no file on disk: {sorted(missing_files)}"
        )

    pending = [m for m in migrations if m.version not in done]
    if done and pending and pending[0].version < max(done):
        raise MigrationError(
            f"migration {pending[0].version} is older than already-applied {max(done)}; "
            "refusing to apply out of order"
        )

    applied: list[int] = []
    for m in pending:
        sql = m.path.read_text()
        # name and version are validated by MIGRATION_FILE, so this literal is safe.
        record = (
            f"INSERT OR IGNORE INTO schema_migrations (version, name) "
            f"VALUES ({m.version}, '{m.name}');"
        )
        try:
            _run_script(conn, f"{sql}\n{record}")
        except sqlite3.Error as exc:
            raise MigrationError(f"migration {m.path.name} failed: {exc}") from exc
        applied.append(m.version)
    return applied


def run_seed(conn: sqlite3.Connection, path: str | Path) -> None:
    """Run one seed file atomically."""
    try:
        _run_script(conn, Path(path).read_text())
    except sqlite3.Error as exc:
        raise MigrationError(f"seed {Path(path).name} failed: {exc}") from exc

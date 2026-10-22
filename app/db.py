"""SQLite connection helpers. The schema lives in schema.sql."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import REPO_DIR

DB_PATH = REPO_DIR / "data" / "closer.db"
UPLOADS_DIR = REPO_DIR / "data" / "uploads"  # moment images; served only through the privacy check
SCHEMA_PATH = Path(__file__).with_name("schema.sql")
SCHEMA_VERSION = 6  # keep in sync with PRAGMA user_version in schema.sql


class SchemaOutdatedError(RuntimeError):
    """The database file was made by an older version of the schema."""


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open the database (default data/closer.db) with named rows and foreign keys enforced."""
    conn = sqlite3.connect(db_path or DB_PATH)  # DB_PATH read at call time so tests can swap it
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create any missing tables. Safe to run on an existing database of the same version."""
    has_tables = conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'couples'").fetchone()
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if has_tables and version != SCHEMA_VERSION:
        raise SchemaOutdatedError(
            f"data/closer.db uses schema version {version}, but this code needs {SCHEMA_VERSION}. "
            "Rebuild the demo database with: python -m app.seed --reset"
        )
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    conn.commit()


@contextmanager
def session(db_path: Path | str | None = None) -> Iterator[sqlite3.Connection]:
    """Connection that commits on success, rolls back on error, and always closes."""
    conn = connect(db_path)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()

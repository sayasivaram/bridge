"""SQLite connection helpers. The schema lives in schema.sql."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import REPO_DIR

DB_PATH = REPO_DIR / "data" / "closer.db"
SCHEMA_PATH = Path(__file__).with_name("schema.sql")


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open the database (default data/closer.db) with named rows and foreign keys enforced."""
    conn = sqlite3.connect(db_path or DB_PATH)  # DB_PATH read at call time so tests can swap it
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    """Create any missing tables. Safe to run on an existing database."""
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

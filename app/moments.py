"""Moment Jar data access. This is the ONLY module that reads the moments table.

Privacy rule, applied inside every SQL query here (not in the UI):
  * A viewer gets shared moments from their couple plus their OWN private moments.
    A partner's private moments are never returned, whatever the caller asks for.
  * Moments headed into a prompt are chosen by who will see the model's output:
      - output_for=<user id>: shared moments + that user's own private moments
      - output_for=BOTH:      shared moments only (output both partners will see)
    So a private moment can only ever reach a prompt whose output goes to its author.

tests/test_moments_privacy.py checks both rules and that no other module queries the table.
"""

from __future__ import annotations

import base64
import binascii
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from app import db

MOODS = ("funny", "sweet", "stressful", "proud", "random")
BOTH = "both"  # output_for value: the output will be shown to both partners
MAX_IMAGE_BYTES = 2 * 1024 * 1024
_IMAGE_TYPES = {  # magic bytes -> file extension; anything else is rejected
    b"\xff\xd8\xff": "jpg",
    b"\x89PNG\r\n\x1a\n": "png",
}

_SELECT = """
    SELECT m.id, m.couple_id, m.author_id, a.name AS author_name, m.mood, m.text, m.song,
           m.image_file, m.visibility, m.created_at
    FROM moments m
    JOIN users a ON a.id = m.author_id
"""
# The privacy rule as SQL. :viewer must belong to the same couple, and private rows pass only
# when the viewer wrote them.
_VISIBLE_TO_VIEWER = """
    m.couple_id = (SELECT couple_id FROM users WHERE id = :viewer)
    AND (m.visibility = 'shared' OR m.author_id = :viewer)
"""


class MomentError(ValueError):
    """Invalid moment input. The message is safe to show to the user."""


def monday_of(d: date) -> date:
    return d - timedelta(days=d.weekday())


def _public(row: sqlite3.Row, viewer_id: int) -> dict:
    m = dict(row)
    m["is_mine"] = m["author_id"] == viewer_id
    m["has_image"] = m.pop("image_file") is not None
    return m


def list_for_viewer(conn: sqlite3.Connection, viewer_id: int, week_of: date) -> list[dict]:
    """The viewer's jar for one Monday-to-Sunday week, newest first."""
    start = monday_of(week_of)
    rows = conn.execute(
        _SELECT + f"WHERE {_VISIBLE_TO_VIEWER} AND date(m.created_at) BETWEEN :start AND :end "
        "ORDER BY m.created_at DESC, m.id DESC",
        {"viewer": viewer_id, "start": start.isoformat(), "end": (start + timedelta(days=6)).isoformat()},
    ).fetchall()
    return [_public(r, viewer_id) for r in rows]


def get_for_viewer(conn: sqlite3.Connection, viewer_id: int, moment_id: int) -> dict | None:
    """One moment, or None if it doesn't exist OR the viewer isn't allowed to see it."""
    row = conn.execute(
        _SELECT + f"WHERE m.id = :id AND {_VISIBLE_TO_VIEWER}", {"id": moment_id, "viewer": viewer_id}
    ).fetchone()
    return _public(row, viewer_id) if row else None


def image_path_for_viewer(conn: sqlite3.Connection, viewer_id: int, moment_id: int) -> Path | None:
    row = conn.execute(
        f"SELECT m.image_file FROM moments m WHERE m.id = :id AND {_VISIBLE_TO_VIEWER}",
        {"id": moment_id, "viewer": viewer_id},
    ).fetchone()
    if not row or not row["image_file"]:
        return None
    return db.UPLOADS_DIR / row["image_file"]


def for_prompt(
    conn: sqlite3.Connection,
    couple_id: int,
    output_for: int | Literal["both"],
    since: date,
    until: date,
) -> list[dict]:
    """Moments an agent may put in a prompt whose output goes to `output_for`. Oldest first."""
    params: dict = {"couple": couple_id, "since": since.isoformat(), "until": until.isoformat()}
    if output_for == BOTH:
        rule = "m.visibility = 'shared'"
    else:
        rule = "(m.visibility = 'shared' OR m.author_id = :reader)"
        params["reader"] = int(output_for)
    rows = conn.execute(
        _SELECT + f"WHERE m.couple_id = :couple AND {rule} "
        "AND date(m.created_at) BETWEEN :since AND :until ORDER BY m.created_at, m.id",
        params,
    ).fetchall()
    return [dict(r) for r in rows]


def add(
    conn: sqlite3.Connection,
    author_id: int,
    text: str,
    mood: str,
    visibility: str,
    song: str | None = None,
    image_data_url: str | None = None,
) -> dict:
    """Validate and store a moment. Text is stored as written (only outer whitespace trimmed)."""
    text = (text or "").strip()
    song = (song or "").strip() or None
    if not 1 <= len(text) <= 500:
        raise MomentError("A moment needs some text (up to 500 characters).")
    if mood not in MOODS:
        raise MomentError(f"Mood must be one of: {', '.join(MOODS)}.")
    if visibility not in ("shared", "private"):
        raise MomentError("Visibility must be 'shared' or 'private'.")
    if song and len(song) > 120:
        raise MomentError("Song name is too long (120 characters max).")
    if song and image_data_url:
        raise MomentError("Attach a song or a photo, not both.")

    image_file = _save_image(image_data_url) if image_data_url else None
    couple_id = conn.execute("SELECT couple_id FROM users WHERE id = ?", (author_id,)).fetchone()[0]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    moment_id = conn.execute(
        "INSERT INTO moments (couple_id, author_id, mood, text, song, image_file, visibility, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (couple_id, author_id, mood, text, song, image_file, visibility, now),
    ).lastrowid
    return get_for_viewer(conn, author_id, moment_id)


def delete(conn: sqlite3.Connection, viewer_id: int, moment_id: int) -> bool:
    """Only the author can delete a moment. Returns False if not found or not theirs."""
    row = conn.execute(
        "SELECT image_file FROM moments WHERE id = ? AND author_id = ?", (moment_id, viewer_id)
    ).fetchone()
    if row is None:
        return False
    conn.execute("DELETE FROM moments WHERE id = ?", (moment_id,))
    if row["image_file"]:
        (db.UPLOADS_DIR / row["image_file"]).unlink(missing_ok=True)
    return True


def _save_image(data_url: str) -> str:
    """Decode a base64 data URL, check it really is a JPEG/PNG under 2 MB, save it."""
    header, _, payload = data_url.partition(",")
    if not header.startswith("data:image/") or ";base64" not in header:
        raise MomentError("Photo must be a JPEG or PNG image.")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise MomentError("Couldn't read that photo.") from None
    if len(raw) > MAX_IMAGE_BYTES:
        raise MomentError("Photo is too large (2 MB max).")
    ext = next((e for magic, e in _IMAGE_TYPES.items() if raw.startswith(magic)), None)
    if ext is None:  # checked from the bytes themselves, not the claimed type
        raise MomentError("Photo must be a JPEG or PNG image.")
    db.UPLOADS_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.{ext}"
    (db.UPLOADS_DIR / name).write_bytes(raw)
    return name


def private_texts(conn: sqlite3.Connection, couple_id: int) -> list[str]:
    """Every private moment's text in a couple. For OUTPUT leak checks only (never into a prompt)."""
    rows = conn.execute(
        "SELECT m.text FROM moments m WHERE m.couple_id = ? AND m.visibility = 'private'", (couple_id,)
    ).fetchall()
    return [r[0] for r in rows]

"""Ritual Builder data: the catalog (data/ritual_catalog.json) and the couple's rituals + ratings.

Feedback notes are visible to both partners (the UI says so), EXCEPT notes that trip the crisis
pre-check: those are stored with flagged = 1, hidden from the partner, and never sent to a model.
"""

from __future__ import annotations

import json
import sqlite3
from functools import cache

from app import safety, timeutil
from app.config import REPO_DIR

CATALOG_PATH = REPO_DIR / "data" / "ritual_catalog.json"
TIME_OPTIONS = {"15": 20, "60": 70, "120": None}  # "15 min" fits <= 20 min, "1 hr" <= 70, "2+ hrs" anything
ENERGY_OPTIONS = {"low": {"low"}, "medium": {"low", "medium"}, "high": {"low", "medium", "high"}}
NOTE_MAX = 200


class RitualError(ValueError):
    """Invalid input. The message is safe to show to the user."""


@cache
def catalog() -> dict[str, dict]:
    """{id: ritual} from the catalog file."""
    items = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))["rituals"]
    return {r["id"]: r for r in items}


def fits(item: dict, time: str, energy: str) -> bool:
    limit = TIME_OPTIONS[time]
    return (limit is None or item["minutes"] <= limit) and item["energy"] in ENERGY_OPTIONS[energy]


def candidates(time: str, energy: str) -> list[dict]:
    """Catalog rituals that fit tonight's time and energy (filtered in code, not by the model)."""
    if time not in TIME_OPTIONS or energy not in ENERGY_OPTIONS:
        raise RitualError("Pick how much time you have and your energy level.")
    return [r for r in catalog().values() if fits(r, time, energy)]


def _couple(conn: sqlite3.Connection, user_id: int) -> int:
    return conn.execute("SELECT couple_id FROM users WHERE id = ?", (user_id,)).fetchone()[0]


def list_for_viewer(conn: sqlite3.Connection, viewer_id: int) -> list[dict]:
    """The couple's rituals, newest first, with both partners' ratings. The partner's note is
    left out if it was flagged by the crisis pre-check."""
    rows = conn.execute(
        """
        SELECT r.*, mine.rating AS my_rating, mine.note AS my_note,
               theirs.rating AS partner_rating,
               CASE WHEN theirs.flagged = 0 THEN theirs.note END AS partner_note
        FROM rituals r
        LEFT JOIN ritual_feedback mine ON mine.ritual_id = r.id AND mine.user_id = :viewer
        LEFT JOIN ritual_feedback theirs ON theirs.ritual_id = r.id AND theirs.user_id != :viewer
        WHERE r.couple_id = (SELECT couple_id FROM users WHERE id = :viewer)
        ORDER BY r.created_at DESC, r.id DESC
        """,
        {"viewer": viewer_id},
    ).fetchall()
    return [dict(r) for r in rows]


def history_for_prompt(conn: sqlite3.Connection, couple_id: int, n: int = 2) -> dict[str, list[dict]]:
    """The feedback loop: the n highest- and n lowest-rated rituals (average of both partners'
    ratings), with unflagged notes, plus the most recent rituals the couple planned or skipped."""
    rated = [dict(r) for r in conn.execute(
        """
        SELECT r.id, r.catalog_id, r.title, r.variation, r.status, r.done_on,
               ROUND(AVG(f.rating), 1) AS avg_rating, COUNT(f.id) AS ratings,
               GROUP_CONCAT(u.name || ' ' || f.rating || '/5' ||
                            CASE WHEN f.note IS NOT NULL AND f.flagged = 0 THEN ': ' || f.note ELSE '' END, ' | ') AS feedback
        FROM rituals r
        JOIN ritual_feedback f ON f.ritual_id = r.id
        JOIN users u ON u.id = f.user_id
        WHERE r.couple_id = ?
        GROUP BY r.id
        ORDER BY avg_rating DESC, r.done_on DESC
        """, (couple_id,))]
    highest = rated[:n]
    lowest = [r for r in reversed(rated) if r not in highest][:n]
    recent = [dict(r) for r in conn.execute(
        "SELECT id, catalog_id, title, variation, status FROM rituals WHERE couple_id = ? "
        "AND status IN ('planned', 'skipped') ORDER BY created_at DESC LIMIT 3", (couple_id,))]
    return {"highest": highest, "lowest": lowest, "recent": recent}


def add_planned(conn: sqlite3.Connection, user_id: int, catalog_id: str, variation: str | None,
                source: str = "agent") -> dict:
    item = catalog().get(catalog_id)
    if item is None:
        raise RitualError("That ritual isn't in the catalog.")
    variation = " ".join((variation or "").split())[:300] or None
    ritual_id = conn.execute(
        "INSERT INTO rituals (couple_id, catalog_id, title, variation, category, description, minutes, energy, "
        "status, source, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'planned', ?, ?)",
        (_couple(conn, user_id), catalog_id, item["name"], variation, item["category"], item["description"],
         item["minutes"], item["energy"], source, timeutil.to_stored(timeutil.now_utc())),
    ).lastrowid
    return next(r for r in list_for_viewer(conn, user_id) if r["id"] == ritual_id)


def _own_ritual(conn: sqlite3.Connection, user_id: int, ritual_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM rituals WHERE id = ? AND couple_id = ?",
                       (ritual_id, _couple(conn, user_id))).fetchone()
    if row is None:
        raise RitualError("Ritual not found.")
    return row


def set_status(conn: sqlite3.Connection, user_id: int, ritual_id: int, status: str) -> None:
    if status not in ("planned", "done", "skipped"):
        raise RitualError("Status must be planned, done, or skipped.")
    _own_ritual(conn, user_id, ritual_id)
    tz = conn.execute("SELECT timezone FROM users WHERE id = ?", (user_id,)).fetchone()[0]
    conn.execute("UPDATE rituals SET status = ?, done_on = ? WHERE id = ?",
                 (status, timeutil.local_today(tz).isoformat() if status == "done" else None, ritual_id))


def rate(conn: sqlite3.Connection, user_id: int, ritual_id: int, rating: int, note: str | None) -> dict:
    """Rate a ritual you did (rating a planned one marks it done). Re-rating updates it."""
    if not isinstance(rating, int) or not 1 <= rating <= 5:
        raise RitualError("Rate it from 1 to 5.")
    note = " ".join((note or "").split()) or None
    if note and len(note) > NOTE_MAX:
        raise RitualError(f"Keep the note to one line ({NOTE_MAX} characters max).")
    ritual = _own_ritual(conn, user_id, ritual_id)
    if ritual["status"] != "done":
        set_status(conn, user_id, ritual_id, "done")
    flag = safety.precheck(note)
    conn.execute(
        "INSERT INTO ritual_feedback (ritual_id, user_id, rating, note, flagged, created_at) VALUES (?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (ritual_id, user_id) DO UPDATE SET rating = excluded.rating, note = excluded.note, "
        "flagged = excluded.flagged, created_at = excluded.created_at",
        (ritual_id, user_id, rating, note, int(flag is not None), timeutil.to_stored(timeutil.now_utc())),
    )
    tz = conn.execute("SELECT timezone FROM users WHERE id = ?", (user_id,)).fetchone()[0]
    return {"ritual_id": ritual_id, "rating": rating, "note": note,
            "support": safety.support_message(flag, tz) if flag else None}

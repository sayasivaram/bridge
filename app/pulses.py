"""Connection Pulse data access. This is the ONLY module that reads the pulses table.

Reveal rule, applied inside the SQL (not in the UI):
  * You always see your own pulse.
  * You see your partner's score and contact answer for a week only once BOTH of you have
    submitted that week (no peeking, no anchoring).
  * You see your partner's note only if, on top of that, they chose to share it.
Data for the Drift Detector (which writes a nudge both partners see) follows the same rule:
only weeks both submitted, and only shared notes. Private notes are read in exactly two places,
both code-only and never sent to a model: the crisis pre-check and output leak checks.

tests/test_pulse.py checks the rule and that no other module queries the table.
"""

from __future__ import annotations

import sqlite3
from datetime import date

from app import timeutil
from app.moments import monday_of

CONTACT = ("too_little", "right", "too_much")
NOTE_MAX = 200  # "one line"


class PulseError(ValueError):
    """Invalid pulse input. The message is safe to show to the user."""


def _user(conn: sqlite3.Connection, user_id: int) -> sqlite3.Row:
    return conn.execute("SELECT id, couple_id, name, timezone FROM users WHERE id = ?", (user_id,)).fetchone()


def current_week(conn: sqlite3.Connection, user_id: int) -> date:
    """Monday of this week in the user's own time zone."""
    return monday_of(timeutil.local_today(_user(conn, user_id)["timezone"]))


def clean_note(note: str | None) -> str | None:
    note = " ".join((note or "").split())  # one line: newlines and runs of spaces collapse
    if len(note) > NOTE_MAX:
        raise PulseError(f"Keep the note to one line ({NOTE_MAX} characters max).")
    return note or None


def upsert(conn: sqlite3.Connection, user_id: int, score: int, contact: str, note: str | None,
           note_shared: bool) -> dict:
    """Save this week's pulse (or update it, if you already checked in this week)."""
    if not isinstance(score, int) or not 1 <= score <= 5:
        raise PulseError("Pick a score from 1 to 5.")
    if contact not in CONTACT:
        raise PulseError("Pick too little, about right, or too much.")
    note = clean_note(note)
    user = _user(conn, user_id)
    week = current_week(conn, user_id).isoformat()
    conn.execute(
        "INSERT INTO pulses (couple_id, user_id, week_start, score, contact, note, note_shared, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT (user_id, week_start) DO UPDATE SET "
        "score = excluded.score, contact = excluded.contact, note = excluded.note, "
        "note_shared = excluded.note_shared, created_at = excluded.created_at",
        (user["couple_id"], user_id, week, score, contact, note, int(bool(note_shared and note)),
         timeutil.to_stored(timeutil.now_utc())),
    )
    return {"week_start": week, "score": score, "contact": contact, "note": note,
            "note_shared": bool(note_shared and note)}


def history_for_viewer(conn: sqlite3.Connection, viewer_id: int, weeks: list[date]) -> list[dict]:
    """One row per week, oldest first, with the reveal rule applied in SQL."""
    if not weeks:
        return []
    values = ", ".join("(?)" for _ in weeks)
    rows = conn.execute(
        f"""
        WITH weeks(week_start) AS (VALUES {values}),
             viewer AS (SELECT id, couple_id FROM users WHERE id = ?),
             partner AS (SELECT u.id FROM users u, viewer v WHERE u.couple_id = v.couple_id AND u.id != v.id)
        SELECT w.week_start,
               me.score AS my_score, me.contact AS my_contact, me.note AS my_note, me.note_shared AS my_note_shared,
               p.id IS NOT NULL AS partner_submitted,
               CASE WHEN me.id IS NOT NULL AND p.id IS NOT NULL THEN p.score END AS partner_score,
               CASE WHEN me.id IS NOT NULL AND p.id IS NOT NULL THEN p.contact END AS partner_contact,
               CASE WHEN me.id IS NOT NULL AND p.id IS NOT NULL AND p.note_shared = 1 THEN p.note END AS partner_note
        FROM weeks w
        LEFT JOIN pulses me ON me.user_id = (SELECT id FROM viewer) AND me.week_start = w.week_start
        LEFT JOIN pulses p ON p.user_id = (SELECT id FROM partner) AND p.week_start = w.week_start
        ORDER BY w.week_start
        """,
        [d.isoformat() for d in weeks] + [viewer_id],
    ).fetchall()
    out = []
    for r in rows:
        mine = r["my_score"] is not None
        status = ("shown" if mine and r["partner_submitted"] else
                  "waiting_for_partner" if mine else
                  "waiting_for_you" if r["partner_submitted"] else "none")
        out.append({
            "week_start": r["week_start"],
            "me": {"score": r["my_score"], "contact": r["my_contact"], "note": r["my_note"],
                   "note_shared": bool(r["my_note_shared"])} if mine else None,
            "partner": {"score": r["partner_score"], "contact": r["partner_contact"], "note": r["partner_note"]}
            if status == "shown" else None,
            "partner_status": status,
        })
    return out


def revealed_for_couple(conn: sqlite3.Connection, couple_id: int, weeks: list[date]) -> list[dict]:
    """For the Drift Detector: weeks where BOTH partners submitted, with shared notes only.
    Returns [{week_start, by_user: {user_id: {score, contact, shared_note}}}], oldest first."""
    if not weeks:
        return []
    rows = conn.execute(
        f"""
        SELECT p.week_start, p.user_id, p.score, p.contact,
               CASE WHEN p.note_shared = 1 THEN p.note END AS shared_note
        FROM pulses p
        WHERE p.couple_id = ? AND p.week_start IN ({", ".join("?" for _ in weeks)})
          AND (SELECT COUNT(*) FROM pulses q WHERE q.couple_id = p.couple_id AND q.week_start = p.week_start) = 2
        ORDER BY p.week_start
        """,
        [couple_id] + [d.isoformat() for d in weeks],
    ).fetchall()
    by_week: dict[str, dict] = {}
    for r in rows:
        by_week.setdefault(r["week_start"], {})[r["user_id"]] = {
            "score": r["score"], "contact": r["contact"], "shared_note": r["shared_note"]}
    return [{"week_start": w, "by_user": u} for w, u in by_week.items()]


def last_checkin(conn: sqlite3.Connection, couple_id: int) -> dict[int, str]:
    """Each partner's most recent pulse time (UTC). Whether someone checked in is visible to both."""
    rows = conn.execute("SELECT user_id, MAX(created_at) FROM pulses WHERE couple_id = ? GROUP BY user_id",
                        (couple_id,)).fetchall()
    return {r[0]: r[1] for r in rows}


def notes_for_safety_check(conn: sqlite3.Connection, couple_id: int, since_week: date) -> list[dict]:
    """ALL recent notes, private included, for the code-only crisis pre-check. Never put these
    in a prompt or show them to the other partner."""
    rows = conn.execute(
        "SELECT user_id, week_start, note FROM pulses WHERE couple_id = ? AND week_start >= ? AND note IS NOT NULL",
        (couple_id, since_week.isoformat()),
    ).fetchall()
    return [dict(r) for r in rows]


def private_notes(conn: sqlite3.Connection, couple_id: int) -> list[str]:
    """Private note texts, for OUTPUT leak checks only (never into a prompt)."""
    rows = conn.execute("SELECT note FROM pulses WHERE couple_id = ? AND note_shared = 0 AND note IS NOT NULL",
                        (couple_id,)).fetchall()
    return [r[0] for r in rows]

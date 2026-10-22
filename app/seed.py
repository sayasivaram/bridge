"""Load the demo couple from data/seed_couple.json.

    python -m app.seed           # create data/closer.db with the demo couple (if empty)
    python -m app.seed --reset   # wipe the database and start over

Dates are relative to today, so the demo always shows "the last three weeks":
week 1 starts on the Monday three weeks ago, week 3 is last week. A few moments land earlier
this week so the jar isn't empty. The current week has no pulse yet, so you can do a live
check-in in the demo. Seed times are stored as UTC.
"""

from __future__ import annotations

import argparse
import json
import shutil
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from app import db
from app.config import REPO_DIR
from app.db import DB_PATH, init_db, session

SEED_PATH = REPO_DIR / "data" / "seed_couple.json"


def week_start(week: int, today: date) -> date:
    """Monday of seed week 1, 2, or 3 (week 3 = last week)."""
    this_monday = today - timedelta(days=today.weekday())
    return this_monday - timedelta(weeks=4 - week)


def seed(db_path: Path | str = DB_PATH, today: date | None = None, reset: bool = False) -> dict[str, int]:
    """Create the schema and load the demo couple. Returns row counts per table."""
    now = datetime.now(timezone.utc).replace(tzinfo=None) if today is None else datetime.combine(today, time(12))
    today = now.date()
    data = json.loads(SEED_PATH.read_text(encoding="utf-8"))
    db_path = Path(db_path)
    if reset:
        db_path.unlink(missing_ok=True)
        if db_path.resolve() == DB_PATH.resolve():
            shutil.rmtree(db.UPLOADS_DIR, ignore_errors=True)  # photos belong to the old database

    with session(db_path) as conn:
        init_db(conn)
        if conn.execute("SELECT COUNT(*) FROM couples").fetchone()[0]:
            return counts(conn)  # already seeded; leave the user's data alone

        c = data["couple"]
        couple_id = conn.execute(
            "INSERT INTO couples (name, together_since, apart_since, profile, inside_jokes) VALUES (?, ?, ?, ?, ?)",
            (c["name"], c["together_since"], c["apart_since"], c["profile"], "\n".join(c.get("inside_jokes", []))),
        ).lastrowid

        user_ids = {}
        for u in data["users"]:
            user_ids[u["role"]] = conn.execute(
                "INSERT INTO users (couple_id, role, name, city, timezone, shared_stressors) VALUES (?, ?, ?, ?, ?, ?)",
                (couple_id, u["role"], u["name"], u["city"], u["timezone"], u.get("shared_stressors", "")),
            ).lastrowid

        def when(item: dict) -> str:
            day = week_start(item["week"], today) + timedelta(days=item["day"])
            return f"{day.isoformat()} {item.get('time', '12:00')}:00"

        # Monday 12:00 UTC is Monday for both partners (8am New Haven, 1pm London).
        this_monday = datetime.combine(today - timedelta(days=today.weekday()), time(12))
        for i, m in enumerate(data["moments"] + data["recent_moments"]):
            if "hours_ago" in m:  # this week's moments: relative to now, but not before Monday (local)
                stamp = max(now - timedelta(hours=m["hours_ago"]), this_monday + timedelta(minutes=i))
                stamp = min(stamp, now - timedelta(minutes=len(data["recent_moments"]) - i))  # never future
                created = stamp.strftime("%Y-%m-%d %H:%M:%S")
            else:
                created = when(m)
            conn.execute(
                "INSERT INTO moments (couple_id, author_id, mood, text, song, visibility, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (couple_id, user_ids[m["who"]], m["mood"], m["text"], m.get("song"), m["visibility"], created),
            )

        for p in data["pulses"]:
            start = week_start(p["week"], today)
            conn.execute(
                "INSERT INTO pulses (couple_id, user_id, week_start, score, contact, note, note_shared, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (couple_id, user_ids[p["who"]], start.isoformat(), p["score"], p.get("contact", "right"), p["note"],
                 int(p["note_shared"]), f"{(start + timedelta(days=6)).isoformat()} 20:00:00"),
            )

        for r in data["rituals"]:
            day = (week_start(r["week"], today) + timedelta(days=r["day"])).isoformat()
            ritual_id = conn.execute(
                "INSERT INTO rituals (couple_id, title, category, description, minutes, energy, "
                "status, source, done_on, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 'seed', ?, ?)",
                (couple_id, r["title"], r["category"], r["description"], r["minutes"], r["energy"],
                 r["status"], day if r["status"] == "done" else None, f"{day} 09:00:00"),
            ).lastrowid
            for f in r["feedback"]:
                conn.execute(
                    "INSERT INTO ritual_feedback (ritual_id, user_id, rating, would_repeat, comment, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (ritual_id, user_ids[f["who"]], f["rating"], int(f["would_repeat"]), f["comment"],
                     f"{day} 22:00:00"),
                )
        return counts(conn)


def counts(conn) -> dict[str, int]:
    tables = ["couples", "users", "moments", "pulses", "rituals", "ritual_feedback", "llm_calls", "nudges"]
    return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}


def main() -> None:
    parser = argparse.ArgumentParser(description="Load the Closer demo couple.")
    parser.add_argument("--reset", action="store_true", help="delete the database first")
    args = parser.parse_args()
    result = seed(reset=args.reset)
    print(f"Database: {DB_PATH}")
    for table, n in result.items():
        print(f"  {table:<16} {n:>3} rows")


if __name__ == "__main__":
    main()

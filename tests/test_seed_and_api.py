"""Tests for the seed data and the switch-user API."""

from fastapi.testclient import TestClient

import app.main
from app.db import session
from app.seed import seed, week_start
from tests.conftest import TODAY


def test_seed_counts(seeded_db):
    with session(seeded_db) as conn:
        n = {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
             for t in ("couples", "users", "moments", "pulses", "rituals", "ritual_feedback")}
    assert n == {"couples": 1, "users": 2, "moments": 26, "pulses": 6, "rituals": 5, "ritual_feedback": 6}


def test_seed_covers_last_three_weeks_and_leaves_this_week_open(seeded_db):
    with session(seeded_db) as conn:
        first, last = conn.execute("SELECT MIN(date(created_at)), MAX(date(created_at)) FROM moments").fetchone()
        weeks = [r[0] for r in conn.execute("SELECT DISTINCT week_start FROM pulses ORDER BY 1")]

    assert first >= week_start(1, TODAY).isoformat()
    assert last < TODAY.isoformat()  # nothing in the future
    assert weeks == ["2026-09-28", "2026-10-05", "2026-10-12"]  # no pulse yet for the current week


def test_seed_has_a_drift_signal_for_the_pulse_agent(seeded_db):
    with session(seeded_db) as conn:
        leo = conn.execute("SELECT id FROM users WHERE role = 'B'").fetchone()[0]
        scores = [r[0] for r in conn.execute(
            "SELECT score FROM pulses WHERE user_id = ? ORDER BY week_start", (leo,))]
        per_week = [conn.execute(
            "SELECT COUNT(*) FROM moments WHERE author_id = ? AND date(created_at) BETWEEN ? AND date(?, '+6 days')",
            (leo, week_start(w, TODAY).isoformat(), week_start(w, TODAY).isoformat())).fetchone()[0]
            for w in (1, 2, 3)]

    assert scores == [4, 3, 2]
    assert per_week == [6, 4, 2]


def test_seed_includes_private_content_for_privacy_tests(seeded_db):
    with session(seeded_db) as conn:
        private_moments = conn.execute("SELECT COUNT(*) FROM moments WHERE visibility = 'private'").fetchone()[0]
        private_notes = conn.execute(
            "SELECT COUNT(*) FROM pulses WHERE note IS NOT NULL AND note_shared = 0").fetchone()[0]
    assert private_moments >= 2 and private_notes >= 2


def test_seed_is_idempotent(seeded_db):
    assert seed(seeded_db, today=TODAY)["moments"] == 26  # running again adds nothing


def test_users_endpoint_lists_both_partners(seeded_db):
    users = TestClient(app.main.app).get("/api/users").json()
    assert [(u["role"], u["name"]) for u in users] == [("A", "Priya"), ("B", "Leo")]


def test_me_endpoint_follows_the_switch_user_header(seeded_db):
    client = TestClient(app.main.app)

    priya = client.get("/api/me", headers={"X-User-Id": "1"}).json()
    leo = client.get("/api/me", headers={"X-User-Id": "2"}).json()

    assert (priya["user"]["name"], priya["partner"]["name"]) == ("Priya", "Leo")
    assert (leo["user"]["name"], leo["partner"]["name"]) == ("Leo", "Priya")
    assert client.get("/api/me").status_code == 400  # no user chosen

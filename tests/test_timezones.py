"""Time zones: "today" and "this week" are in each partner's own time zone, while timestamps are
stored in UTC. Regression test for the bug where a moment added in the evening in New Haven
(already tomorrow in UTC) went missing from this week's jar and from call prep.
"""

from datetime import date, datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app.main
from app import timeutil
from app.agents.call_topics import build_prompt, run
from app.config import MODEL, Settings
from app.db import session
from app.llm import LLM
from tests.conftest import FakeClient

PRIYA, LEO = 1, 2  # America/New_York and Europe/London in the seed
SUNDAY_9PM_NEW_HAVEN = datetime(2026, 10, 26, 1, 0, tzinfo=timezone.utc)  # = Sun Oct 25, 21:00 EDT
EVENING_TEXT = "Booked a table at our usual video-dinner spot for next Sunday."


@pytest.fixture
def sunday_evening(seeded_db, monkeypatch):
    """Clock frozen at Sunday 9pm in New Haven; Priya adds a shared moment through the API."""
    monkeypatch.setattr(timeutil, "now_utc", lambda: SUNDAY_9PM_NEW_HAVEN)
    r = TestClient(app.main.app).post("/api/moments", headers={"X-User-Id": str(PRIYA)},
                                      json={"text": EVENING_TEXT, "mood": "sweet", "visibility": "shared"})
    assert r.status_code == 201
    return seeded_db, r.json()


def test_evening_moment_is_stored_in_utc_on_the_next_day(sunday_evening):
    _, moment = sunday_evening
    assert moment["created_at"] == "2026-10-26 01:00:00"  # Monday in UTC...


def test_evening_moment_shows_in_this_weeks_jar(sunday_evening):
    _, moment = sunday_evening
    jar = TestClient(app.main.app).get("/api/moments", headers={"X-User-Id": str(PRIYA)}).json()

    assert jar["week_start"] == "2026-10-19" and jar["is_current_week"]  # ...but still Sunday for Priya
    assert moment["id"] in {m["id"] for m in jar["moments"]}


def test_each_partner_gets_their_own_local_week(sunday_evening):
    """At the same instant it's already Monday in London, so Leo's 'this week' has started."""
    _, moment = sunday_evening
    client = TestClient(app.main.app)
    leo_now = client.get("/api/moments", headers={"X-User-Id": str(LEO)}).json()
    leo_last_week = client.get("/api/moments?week_of=2026-10-19", headers={"X-User-Id": str(LEO)}).json()

    assert leo_now["week_start"] == "2026-10-26"
    assert moment["id"] in {m["id"] for m in leo_now["moments"]}  # 01:00 Monday, London time
    assert moment["id"] not in {m["id"] for m in leo_last_week["moments"]}


def test_evening_moment_is_in_call_prep(sunday_evening):
    db_path, moment = sunday_evening
    with session(db_path) as conn:
        p = build_prompt(conn, PRIYA, timeutil.local_today("America/New_York"))

    assert p["until"] == date(2026, 10, 25)  # Priya's local today, not UTC's Monday
    assert moment["id"] in {m["id"] for m in p["moments"]}
    assert f'<moment id="{moment["id"]}"' in p["user_input"] and 'date="2026-10-25"' in p["user_input"]


def test_call_prep_uses_the_requesters_local_today_by_default(sunday_evening):
    db_path, _ = sunday_evening
    client = FakeClient()
    run(LLM(Settings(provider="portkey", api_key="k", model=MODEL, env_files_loaded=()), db_path=db_path,
            client=client), PRIYA)

    assert "Today is Sunday, October 25, 2026" in client.requests[0]["input"]
    assert EVENING_TEXT in client.requests[0]["input"]


def test_local_days_are_right_across_daylight_saving_changes():
    def hours(day, tz):
        start, end = (datetime.strptime(s, timeutil.STORED) for s in timeutil.utc_bounds(day, day, tz))
        return (end - start).total_seconds() / 3600

    assert hours(date(2026, 10, 25), "Europe/London") == 25  # clocks go back in the UK
    assert hours(date(2026, 11, 1), "America/New_York") == 25  # and a week later in the US
    assert hours(date(2026, 10, 25), "America/New_York") == 24
    assert timeutil.utc_bounds(date(2026, 10, 25), date(2026, 10, 25), "America/New_York") == (
        "2026-10-25 04:00:00", "2026-10-26 04:00:00")


def test_unknown_time_zone_falls_back_to_utc():
    assert timeutil.utc_bounds(date(2026, 10, 25), date(2026, 10, 25), "Mars/Olympus_Mons") == (
        "2026-10-25 00:00:00", "2026-10-26 00:00:00")

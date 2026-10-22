"""Connection Pulse: reveal rule (privacy), drift rules, the drift agent's output checks, and the
coach-boundary crisis pre-check. Fake model client throughout, so no tokens are spent."""

import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.main
from app import pulses, safety, timeutil
from app.agents import drift
from app.config import MODEL, Settings
from app.db import session
from app.llm import LLM
from tests.conftest import FakeClient, fake_response

PRIYA, LEO = 1, 2
WEDNESDAY_NOON_NY = datetime(2026, 10, 21, 16, 0, tzinfo=timezone.utc)  # week of Oct 19 for both partners
SETTINGS = Settings(provider="portkey", api_key="k", model=MODEL, env_files_loaded=())
H = {PRIYA: {"X-User-Id": "1"}, LEO: {"X-User-Id": "2"}}
CRISIS_NOTE = "I'm scared of what he'll do when he's angry."


@pytest.fixture
def db(seeded_db, monkeypatch):
    monkeypatch.setattr(timeutil, "now_utc", lambda: WEDNESDAY_NOON_NY)
    return seeded_db


@pytest.fixture
def api():
    return TestClient(app.main.app)


def use_fake_llm(monkeypatch, db, client):
    monkeypatch.setattr(app.main, "_llm", LLM(SETTINGS, db_path=db, client=client))
    return client


def nudge_answer(suggestion="Bring back the Sunday cook-along this weekend, just the two of you and one recipe.",
                 explanation="You both said contact felt like too little, and Leo's score went 4 → 3 → 2.",
                 used=("contact_too_little", "score_falling")):
    return fake_response(text=json.dumps({"suggestion": suggestion, "explanation": explanation,
                                          "signals_used": list(used)}))


def checkin(api, who, score=4, contact="right", note=None, shared=False):
    r = api.post("/api/pulse", headers=H[who], json={"score": score, "contact": contact, "note": note,
                                                     "note_shared": shared})
    assert r.status_code == 200, r.text
    return r.json()


# --- Privacy: the reveal rule ----------------------------------------------------------------

def test_partner_score_hidden_until_both_check_in(db, api):
    checkin(api, PRIYA, score=5)

    priya = api.get("/api/pulse", headers=H[PRIYA]).json()["this_week"]
    leo = api.get("/api/pulse", headers=H[LEO]).json()["this_week"]
    assert priya["partner"] is None and priya["partner_status"] == "waiting_for_partner"
    assert leo["me"] is None and leo["partner"] is None and leo["partner_status"] == "waiting_for_you"

    checkin(api, LEO, score=2)
    priya = api.get("/api/pulse", headers=H[PRIYA]).json()["this_week"]
    leo = api.get("/api/pulse", headers=H[LEO]).json()["this_week"]
    assert priya["partner"]["score"] == 2 and leo["partner"]["score"] == 5


def test_private_note_never_shown_even_after_both_check_in(db, api):
    checkin(api, PRIYA, note="Loved the call on Tuesday.", shared=True)
    checkin(api, LEO, note="Worried I'm letting her down.", shared=False)

    for_priya = api.get("/api/pulse", headers=H[PRIYA])
    for_leo = api.get("/api/pulse", headers=H[LEO]).json()["this_week"]
    assert for_priya.json()["this_week"]["partner"]["note"] is None
    assert "Worried I'm letting her down" not in for_priya.text
    assert for_leo["partner"]["note"] == "Loved the call on Tuesday."  # shared notes do show


def test_seed_private_notes_are_hidden_in_history(db, api):
    with session(db) as conn:
        leo_private = [r[0] for r in conn.execute("SELECT note FROM pulses WHERE user_id = 2 AND note_shared = 0 AND note IS NOT NULL")]
    body = api.get("/api/pulse", headers=H[PRIYA]).text
    assert leo_private and not any(n in body for n in leo_private)


def test_only_the_privacy_layers_read_moments_and_pulses():
    app_dir = Path(app.main.__file__).parent
    for table, owner in (("moments", "moments.py"), ("pulses", "pulses.py")):
        reads = re.compile(rf"\b(FROM|JOIN)\s+{table}\b", re.IGNORECASE)
        offenders = [str(p.relative_to(app_dir)) for p in app_dir.rglob("*.py")
                     if p.name != owner and reads.search(p.read_text())]
        assert offenders == [], f"{offenders} query {table} directly instead of via app/{owner}"


def test_checkin_validation(db, api):
    assert api.post("/api/pulse", headers=H[PRIYA], json={"score": 6, "contact": "right"}).status_code == 422
    assert api.post("/api/pulse", headers=H[PRIYA], json={"score": 3, "contact": "lots"}).status_code == 422
    assert api.post("/api/pulse", headers=H[PRIYA], json={"score": 3, "contact": "right", "note": "x" * 201}).status_code == 422
    saved = checkin(api, PRIYA, note="two\nlines   here")["saved"]
    assert saved["note"] == "two lines here" and saved["week_start"] == "2026-10-19"
    assert checkin(api, PRIYA, score=2)["saved"]["score"] == 2  # same week: updates, doesn't duplicate


# --- Drift rules (layer 1) --------------------------------------------------------------------

def test_rules_flag_the_seed_couples_drift(db):
    with session(db) as conn:
        sig = drift.compute_signals(conn, 1, pulses.current_week(conn, PRIYA))
    flags = {(f["key"], f["who"]) for f in sig["flags"]}
    assert flags == {("score_falling", "Leo"), ("fewer_moments", "Leo"), ("contact_too_little", "both")}
    assert sig["partners"]["Leo"]["scores"] == [4, 3, 2]
    assert sig["partners"]["Leo"]["moments_per_week"] == [6, 3, 1]  # shared only: his private ones aren't counted


def make_steady(db):
    with session(db) as conn:
        conn.execute("UPDATE pulses SET score = 4, contact = 'right'")
        for i in range(4):  # Leo keeps sharing moments last week
            conn.execute("INSERT INTO moments (couple_id, author_id, mood, text, visibility, created_at) "
                         "VALUES (1, 2, 'funny', ?, 'shared', '2026-10-14 12:00:00')", (f"steady {i}",))


def test_steady_week_never_calls_the_model(db, api, monkeypatch):
    make_steady(db)
    client = use_fake_llm(monkeypatch, db, FakeClient())

    out = api.get("/api/pulse/nudge", headers=H[PRIYA]).json()

    assert out["kind"] == "steady" and client.requests == []
    assert out["usage"]["calls"] == 0


# --- Drift agent (layer 2) --------------------------------------------------------------------

def test_agent_runs_only_on_drift_with_shared_data_only_and_is_cached(db, api, monkeypatch):
    client = use_fake_llm(monkeypatch, db, FakeClient(response=nudge_answer()))

    first = api.get("/api/pulse/nudge", headers=H[PRIYA]).json()
    again = api.get("/api/pulse/nudge", headers=H[PRIYA]).json()
    leo = api.get("/api/pulse/nudge", headers=H[LEO]).json()

    assert first["kind"] == "nudge" and first["source"] == "llm" and len(client.requests) == 1
    sent = client.requests[0]["instructions"] + client.requests[0]["input"]
    assert "<signals>" in sent and "score_falling" in sent
    assert "Calls felt rushed this week" in sent  # Priya's SHARED note
    with session(db) as conn:
        private = pulses.private_notes(conn, 1) + [r[0] for r in conn.execute(
            "SELECT text FROM moments WHERE visibility = 'private'")]
    assert not any(p in sent for p in private)  # no private notes or moments
    assert client.requests[0]["reasoning"] == {"effort": "low"}
    assert again["cached"] and leo["cached"] and len(client.requests) == 1  # both partners, one model call
    assert leo["suggestion"] == first["suggestion"]


@pytest.mark.parametrize("bad", [
    nudge_answer(suggestion="Honestly, you two should think about whether to break up."),
    nudge_answer(suggestion="Leo seems to be pulling away; Priya should give him space."),
    nudge_answer(explanation="Leo might be depressed, so plan something gentle."),
    nudge_answer(explanation="Your relationship will struggle unless you talk more."),
])
def test_coach_boundary_violations_are_retried_then_replaced(db, monkeypatch, bad):
    client = FakeClient(response=bad)

    out = drift.run(LLM(SETTINGS, db_path=db, client=client), PRIYA)

    assert len(client.requests) == 2  # one retry, never more
    assert out["source"] == "fallback"
    assert not drift.boundary_violations(out["suggestion"] + " " + out["explanation"], ["Priya", "Leo"])
    assert any("coach boundary" in line for line in out["trace"])


def test_invented_numbers_are_rejected(db, monkeypatch):
    client = FakeClient(sequence=[nudge_answer(explanation="Leo's score fell by 7 points since August."),
                                  nudge_answer()])

    out = drift.run(LLM(SETTINGS, db_path=db, client=client), PRIYA)

    assert len(client.requests) == 2 and out["source"] == "llm"
    assert any("numbers that aren't in <signals>" in line for line in out["trace"])


def test_signals_used_must_be_real_flags(db):
    client = FakeClient(response=nudge_answer(used=("score_gap",)))  # not flagged for this couple
    out = drift.run(LLM(SETTINGS, db_path=db, client=client), PRIYA)
    assert out["source"] == "fallback" and len(client.requests) == 2


def test_out_of_budget_still_gets_a_rule_based_nudge(db, api, monkeypatch):
    client = use_fake_llm(monkeypatch, db, FakeClient(response=nudge_answer()))
    with session(db) as conn:
        conn.execute("UPDATE users SET daily_token_budget = 10")

    out = api.get("/api/pulse/nudge", headers=H[PRIYA]).json()

    assert out["kind"] == "nudge" and out["source"] == "fallback" and client.requests == []
    assert "cook-along" not in out["suggestion"] or "5.0/5" in out["suggestion"]  # rituals cited with real ratings


# --- Coach boundary: crisis pre-check ---------------------------------------------------------

@pytest.mark.parametrize("note, category", [
    ("I'm scared of what he'll do when he's angry", "abuse"),
    ("She won't let me see my friends anymore", "abuse"),
    ("Honestly I feel unsafe at home", "abuse"),
    ("I’m terrified of him", "abuse"),  # curly apostrophe
    ("Some days I don't want to be here anymore", "self_harm"),
    ("I keep thinking about hurting myself", "self_harm"),
    ("I can't cope with any of this", "distress"),
    ("Third panic attack this week", "distress"),
])
def test_precheck_flags_concerning_notes(note, category):
    assert safety.precheck(note).category == category


@pytest.mark.parametrize("note", [
    "It hit me how much I miss you", "This deadline is killing me", "I'm a hopeless romantic",
    "Scared of the job market", "Dying to see you in December", "My manager hit me with a new deadline",
    "Felt far away this week. Missed our Sunday cook.", "Calls felt rushed this week.",
])
def test_precheck_ignores_everyday_figures_of_speech(note):
    assert safety.precheck(note) is None


@pytest.mark.parametrize("shared", [False, True])
def test_crisis_note_gets_support_for_its_author_only_and_no_coaching(db, api, monkeypatch, shared):
    client = use_fake_llm(monkeypatch, db, FakeClient(response=nudge_answer()))

    saved = checkin(api, LEO, score=2, contact="too_little", note=CRISIS_NOTE, shared=shared)
    leo = api.get("/api/pulse/nudge", headers=H[LEO]).json()
    priya = api.get("/api/pulse/nudge", headers=H[PRIYA])

    support = saved["support"]  # shown right after Leo submits
    assert support["category"] == "abuse" and "0808 2000 247" in support["resources"][0]["contact"]  # UK line
    assert "999" in support["emergency"] and leo["kind"] == "support"
    # Priya sees exactly what a quiet week looks like: no hint, no text, no coaching
    assert priya.json()["kind"] == "steady" and priya.json()["title"] == drift.QUIET["title"]
    for word in ("scared", "support", "helpline", "safe", "angry", "0808"):
        assert word not in priya.text.lower()
    assert client.requests == []  # the note never went to the model, and no nudge was generated


def test_support_lines_match_the_authors_country():
    flag = safety.SafetyFlag("self_harm", "x")
    assert "988" in safety.support_message(flag, "America/New_York")["resources"][0]["contact"]
    assert "116 123" in safety.support_message(flag, "Europe/London")["resources"][0]["contact"]
    assert "findahelpline.com" in safety.support_message(flag, "Asia/Tokyo")["resources"][0]["contact"]


def test_nudge_cache_ignores_the_daily_days_since_checkin_count(db):
    with session(db) as conn:
        sig = drift.compute_signals(conn, 1, pulses.current_week(conn, PRIYA))
    later = json.loads(json.dumps(sig))
    for p in later["partners"].values():
        p["days_since_checkin"] = (p["days_since_checkin"] or 0) + 1
    assert drift.signals_hash(later) == drift.signals_hash(sig)
    later["flags"].append({"key": "missed_checkins", "who": "Leo", "detail": "Leo hasn't checked in for 10 days."})
    assert drift.signals_hash(later) != drift.signals_hash(sig)

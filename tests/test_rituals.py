"""Ritual Builder: catalog, time/energy filtering, catalog-ID validation, no invented purchases or
bookings, the feedback loop, and flagged feedback notes. Fake model client (no tokens spent)."""

import json
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

import app.main
from app import rituals, timeutil
from app.agents import ritual_builder as rb
from app.config import MODEL, Settings
from app.db import session
from app.llm import LLM
from tests.conftest import FakeClient, fake_response

PRIYA, LEO = 1, 2
H = {PRIYA: {"X-User-Id": "1"}, LEO: {"X-User-Id": "2"}}
SETTINGS = Settings(provider="portkey", api_key="k", model=MODEL, env_files_loaded=())
NOW = datetime(2026, 10, 21, 22, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(seeded_db, monkeypatch):
    monkeypatch.setattr(timeutil, "now_utc", lambda: NOW)
    return seeded_db


@pytest.fixture
def api():
    return TestClient(app.main.app)


def picks(*items):
    """items: (ritual_id, why, first_step, variation)."""
    return fake_response(text=json.dumps({"picks": [
        {"ritual_id": i, "why": w, "first_step": s, "variation": v} for i, w, s, v in items]}))


GOOD = picks(
    ("co-watch-episode", "Bake Off was a 4.0/5 and good for a tired evening.", "Pick the next episode and press play on three.", "Next Bake Off episode"),
    ("drink-tasting", "Same eat-together feel as your 5.0/5 cook-along, with no cooking.", "Each make a cup of tea and call.", ""),
    ("three-good-things", "A gentle way to end a long day.", "Each think of three small good moments from today.", ""),
)


# --- Catalog ----------------------------------------------------------------------------------

def test_catalog_has_25_rituals_across_the_six_categories():
    cat = rituals.catalog()
    assert len(cat) == 25
    assert {r["category"] for r in cat.values()} == {"cook", "watch", "game", "creative", "letters", "goodnight"}
    for r in cat.values():
        assert {"id", "name", "category", "minutes", "energy", "setup", "cost", "description"} <= r.keys()
        assert not rb.spending_problems(f"{r['setup']} {r['description']}", r)  # no bookings in the catalog itself


@pytest.mark.parametrize("time, energy, max_minutes, energies", [
    ("15", "low", 20, {"low"}), ("60", "medium", 70, {"low", "medium"}), ("120", "high", 999, {"low", "medium", "high"})])
def test_candidates_fit_time_and_energy(time, energy, max_minutes, energies):
    found = rituals.candidates(time, energy)
    assert found and all(r["minutes"] <= max_minutes and r["energy"] in energies for r in found)


# --- Prompt -----------------------------------------------------------------------------------

def test_prompt_has_catalog_in_stable_part_and_tonight_in_variable_part(db):
    with session(db) as conn:
        a = rb.build_prompt(conn, PRIYA, "15", "low")
        b = rb.build_prompt(conn, LEO, "120", "high")
    assert a["instructions"] == b["instructions"]  # one cacheable prefix for both partners and any evening
    assert "<ritual_catalog>" in a["instructions"] and "sky-swap" in a["instructions"]
    assert "<allowed_ids>" in a["user_input"] and "movie-night" not in a["user_input"].split("<history>")[0]
    assert "average 5.0/5" in a["user_input"] and "Too much strategy after a long work day" in a["user_input"]


# --- Validation: catalog IDs, fit, no purchases/bookings --------------------------------------

def test_valid_picks_need_one_call(db):
    client = FakeClient(response=GOOD)
    out = rb.run(LLM(SETTINGS, db_path=db, client=client), PRIYA, "60", "low")
    assert len(client.requests) == 1 and [p["source"] for p in out["picks"]] == ["agent"] * 3
    assert client.requests[0]["reasoning"] == {"effort": "low"}
    assert {p["id"] for p in out["picks"]} == {"co-watch-episode", "drink-tasting", "three-good-things"}
    assert all(p["name"] and p["minutes"] for p in out["picks"])  # catalog details merged in


def test_bad_picks_are_dropped_and_retried_once(db):
    first = picks(
        ("candlelit-dinner-out", "Romantic.", "Find a restaurant.", ""),  # not in the catalog
        ("movie-night", "You love watching together.", "Pick a film.", ""),  # 120 min: doesn't fit 15 min
        ("drink-tasting", "Cozy.", "Book a table at a tea room in both cities.", ""),  # booking
        ("video-swap", "Quick and silly.", "Buy a new streaming subscription first.", ""),  # purchase on a free ritual
        ("three-good-things", "You rated Bake Off 4.5/5.", "Think of three good moments.", ""),  # invented number
    )
    second = picks(
        ("story-relay", "Light and quick.", "One of you starts with 'Once upon a time'.", ""),
        ("sky-swap", "London is 5 hours ahead, so your skies look different.", "Step outside and snap the sky.", ""),
        ("read-aloud", "Good for a tired evening, like Bake Off.", "Pick a chapter.", ""),
    )
    client = FakeClient(sequence=[first, second])

    out = rb.run(LLM(SETTINGS, db_path=db, client=client), PRIYA, "15", "low")

    assert len(client.requests) == 2
    assert [p["id"] for p in out["picks"]] == ["story-relay", "sky-swap", "read-aloud"]
    dropped = " ".join(line for line in out["trace"] if "Dropped" in line)
    for reason in ("not in the catalog", "doesn't fit tonight", "purchase or booking", "rating that isn't in the history"):
        assert reason in dropped
    assert "<validation_feedback>" in client.requests[1]["input"]


def test_never_more_than_one_retry_and_gaps_filled_by_rules(db):
    client = FakeClient(response=picks(("made-up-ritual", "x", "y", ""), ("also-made-up", "x", "y", "")))

    out = rb.run(LLM(SETTINGS, db_path=db, client=client), PRIYA, "60", "low")

    assert len(client.requests) == 2
    assert len(out["picks"]) == 3 and all(p["source"] == "rules" for p in out["picks"])
    allowed = {r["id"] for r in rituals.candidates("60", "low")}
    assert all(p["id"] in rituals.catalog() and p["id"] in allowed for p in out["picks"])


def test_every_returned_id_exists_in_the_catalog_even_with_a_hostile_model(db):
    client = FakeClient(response=picks(*[(f"evil-{i}", "x", "y", "") for i in range(3)]))
    out = rb.run(LLM(SETTINGS, db_path=db, client=client), LEO, "120", "high")
    assert {p["id"] for p in out["picks"]} <= set(rituals.catalog())


def test_purchases_are_fine_only_when_the_catalog_ritual_needs_them():
    cat = rituals.catalog()
    assert not rb.spending_problems("Each of you buy the ingredients today.", cat["cook-same-recipe"])
    assert rb.spending_problems("Buy matching mugs first.", cat["drink-tasting"])
    assert rb.spending_problems("Book a table for two.", cat["cook-same-recipe"])  # bookings: never
    assert not rb.spending_problems("Read a chapter from a book you already own.", cat["read-aloud"])


def test_rule_picks_avoid_low_rated_and_favor_high_rated(db):
    with session(db) as conn:
        h = rituals.history_for_prompt(conn, 1)
    out = rb.rule_picks({r["id"] for r in rituals.candidates("120", "high")}, h, set(), 3)
    ids = [p["ritual_id"] for p in out]
    assert "coop-word-game" not in ids  # rated 2.5
    assert "cook-same-recipe" in ids  # rated 5.0


# --- Feedback loop ----------------------------------------------------------------------------

def test_feedback_changes_what_goes_into_the_next_prompt(db, api):
    planned = api.post("/api/rituals", headers=H[PRIYA], json={"catalog_id": "sky-swap"}).json()
    for who, rating, note in ((PRIYA, 5, "Our skies at the same moment!"), (LEO, 5, None)):
        r = api.post(f"/api/rituals/{planned['id']}/feedback", headers=H[who], json={"rating": rating, "note": note})
        assert r.status_code == 200 and r.json()["support"] is None
    bake_off = next(x for x in api.get("/api/rituals", headers=H[PRIYA]).json() if x["catalog_id"] == "co-watch-episode")
    for who in (PRIYA, LEO):
        api.post(f"/api/rituals/{bake_off['id']}/feedback", headers=H[who], json={"rating": 1, "note": "Fell asleep."})

    with session(db) as conn:
        h = rituals.history_for_prompt(conn, 1)
        prompt = rb.build_prompt(conn, PRIYA, "60", "low")["user_input"]
    assert {r["catalog_id"] for r in h["highest"]} == {"cook-same-recipe", "sky-swap"}  # both 5.0 (newer first)
    assert h["lowest"][0]["catalog_id"] == "co-watch-episode"  # re-rated 1/5 by both
    assert "Our skies at the same moment!" in prompt and "Fell asleep." in prompt


def test_rating_marks_done_and_partner_sees_rating_and_note(db, api):
    planned = api.post("/api/rituals", headers=H[PRIYA], json={"catalog_id": "read-aloud", "variation": "Chapter 3"}).json()
    api.post(f"/api/rituals/{planned['id']}/feedback", headers=H[PRIYA], json={"rating": 4, "note": "Your voice is so calming."})

    leo_view = next(x for x in api.get("/api/rituals", headers=H[LEO]).json() if x["id"] == planned["id"])
    assert leo_view["status"] == "done" and leo_view["partner_rating"] == 4
    assert leo_view["partner_note"] == "Your voice is so calming."


def test_flagged_feedback_note_is_hidden_from_partner_and_kept_out_of_prompts(db, api):
    planned = api.post("/api/rituals", headers=H[LEO], json={"catalog_id": "fall-asleep-call"}).json()
    note = "Honestly I feel unsafe at home lately."
    res = api.post(f"/api/rituals/{planned['id']}/feedback", headers=H[LEO], json={"rating": 3, "note": note}).json()

    assert res["support"]["category"] == "abuse" and "999" in res["support"]["emergency"]  # Leo is in London
    priya_view = api.get("/api/rituals", headers=H[PRIYA])
    assert note not in priya_view.text
    with session(db) as conn:
        prompt = rb.build_prompt(conn, PRIYA, "120", "high")
    assert note not in prompt["user_input"] + prompt["instructions"]


def test_plan_rejects_ids_not_in_the_catalog(db, api):
    r = api.post("/api/rituals", headers=H[PRIYA], json={"catalog_id": "skydiving-date"})
    assert r.status_code == 400 and "catalog" in r.json()["detail"]


def test_suggest_endpoint_and_budget_refusal(db, api, monkeypatch):
    client = FakeClient(response=GOOD)
    monkeypatch.setattr(app.main, "_llm", LLM(SETTINGS, db_path=db, client=client))
    ok = api.post("/api/rituals/suggest", headers=H[PRIYA], json={"time": "60", "energy": "low"})
    assert ok.status_code == 200 and len(ok.json()["picks"]) == 3
    assert api.post("/api/rituals/suggest", headers=H[PRIYA], json={"time": "30", "energy": "low"}).status_code == 422

    with session(db) as conn:
        conn.execute("UPDATE users SET daily_token_budget = 100")
    assert api.post("/api/rituals/suggest", headers=H[PRIYA], json={"time": "60", "energy": "low"}).status_code == 429
    assert len(client.requests) == 1


def test_minutes_and_time_differences_are_not_mistaken_for_invented_ratings(db):
    """Live run 1 retried because '10 minutes' was flagged as an invented number. Only ratings count."""
    client = FakeClient(response=picks(
        ("sky-swap", "Takes 10 minutes and London is 5 hours ahead, so your skies differ.", "Snap the sky.", ""),
        ("story-relay", "15 minutes, no prep.", "Start with one sentence.", ""),
        ("co-watch-episode", "Bake Off was a 4/5 for you two.", "Press play on three.", ""),
    ))
    out = rb.run(LLM(SETTINGS, db_path=db, client=client), PRIYA, "60", "low")
    assert len(client.requests) == 1 and all(p["source"] == "agent" for p in out["picks"])

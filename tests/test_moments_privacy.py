"""Safety: privacy between partners.

Proves that the privacy rule lives in the database query layer (app/moments.py):
a partner can never retrieve the other's private moments by any route, and private moments
are never selected for a prompt whose output goes to the other partner.
"""

import base64
import re
from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

import app.main
from app import moments
from app.db import session
from app.prompting import escape, moments_block
from tests.conftest import TODAY

PRIYA, LEO = 1, 2
ALL_TIME = (date(2000, 1, 1), date(2100, 1, 1))
TINY_PNG = "data:image/png;base64," + base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64).decode()


def private_texts(conn, author_id):
    """Ground truth straight from the table (tests may read it directly)."""
    return {r[0] for r in conn.execute(
        "SELECT text FROM moments WHERE author_id = ? AND visibility = 'private'", (author_id,))}


def all_weeks_for(conn, viewer):
    weeks = [r[0] for r in conn.execute("SELECT DISTINCT date(created_at, 'weekday 0', '-6 days') FROM moments")]
    return [m for w in weeks for m in moments.list_for_viewer(conn, viewer, date.fromisoformat(w))]


def test_seed_really_has_private_moments_on_both_sides(seeded_db):
    with session(seeded_db) as conn:
        assert len(private_texts(conn, PRIYA)) >= 2 and len(private_texts(conn, LEO)) >= 2


def test_jar_never_returns_partners_private_moments(seeded_db):
    with session(seeded_db) as conn:
        leo_private, priya_private = private_texts(conn, LEO), private_texts(conn, PRIYA)
        priya_sees = {m["text"] for m in all_weeks_for(conn, PRIYA)}
        leo_sees = {m["text"] for m in all_weeks_for(conn, LEO)}

    assert priya_sees.isdisjoint(leo_private)
    assert leo_sees.isdisjoint(priya_private)
    assert priya_private <= priya_sees  # you still see your own private moments
    assert leo_private <= leo_sees


def test_single_lookup_of_partners_private_moment_returns_nothing(seeded_db):
    with session(seeded_db) as conn:
        leo_private_id = conn.execute(
            "SELECT id FROM moments WHERE author_id = ? AND visibility = 'private'", (LEO,)).fetchone()[0]
        assert moments.get_for_viewer(conn, PRIYA, leo_private_id) is None
        assert moments.get_for_viewer(conn, LEO, leo_private_id) is not None


def test_api_response_never_contains_partners_private_text(seeded_db):
    client = TestClient(app.main.app)
    with session(seeded_db) as conn:
        leo_private = private_texts(conn, LEO)
    bodies = [
        client.get(f"/api/moments?week_of={d}", headers={"X-User-Id": str(PRIYA)}).text
        for d in ("2026-09-28", "2026-10-05", "2026-10-12", TODAY.isoformat())
    ]
    for text in leo_private:
        assert not any(text in body for body in bodies)


def test_private_photo_is_not_served_to_partner(seeded_db):
    client = TestClient(app.main.app)
    as_leo, as_priya = {"X-User-Id": str(LEO)}, {"X-User-Id": str(PRIYA)}
    secret = client.post("/api/moments", headers=as_leo, json={
        "text": "private photo", "mood": "sweet", "visibility": "private", "image": TINY_PNG}).json()
    shared = client.post("/api/moments", headers=as_leo, json={
        "text": "shared photo", "mood": "sweet", "visibility": "shared", "image": TINY_PNG}).json()

    assert client.get(f"/api/moments/{secret['id']}/image", headers=as_priya).status_code == 404
    assert client.get(f"/api/moments/{secret['id']}/image", headers=as_leo).status_code == 200
    assert client.get(f"/api/moments/{shared['id']}/image", headers=as_priya).status_code == 200


def test_partner_cannot_delete_your_moment(seeded_db):
    client = TestClient(app.main.app)
    with session(seeded_db) as conn:
        leo_moment = conn.execute("SELECT id FROM moments WHERE author_id = ? LIMIT 1", (LEO,)).fetchone()[0]

    assert client.delete(f"/api/moments/{leo_moment}", headers={"X-User-Id": str(PRIYA)}).status_code == 404
    with session(seeded_db) as conn:
        assert conn.execute("SELECT 1 FROM moments WHERE id = ?", (leo_moment,)).fetchone()


def test_other_couples_moments_are_invisible(seeded_db):
    with session(seeded_db) as conn:
        other = conn.execute("INSERT INTO couples (name) VALUES ('Other couple')").lastrowid
        stranger = conn.execute(
            "INSERT INTO users (couple_id, role, name) VALUES (?, 'A', 'Stranger')", (other,)).lastrowid
        conn.execute("INSERT INTO moments (couple_id, author_id, mood, text, visibility) "
                     "VALUES (?, ?, 'funny', 'not your couple', 'shared')", (other, stranger))
        assert "not your couple" not in {m["text"] for m in moments.list_for_viewer(conn, PRIYA, date.today())}
        assert "not your couple" not in {m["text"] for m in moments.for_prompt(conn, 1, PRIYA, *ALL_TIME)}


# --- Prompts ---------------------------------------------------------------------------------

def test_prompt_for_one_partner_never_includes_the_others_private_moments(seeded_db):
    with session(seeded_db) as conn:
        leo_private, priya_private = private_texts(conn, LEO), private_texts(conn, PRIYA)
        for_priya = moments.for_prompt(conn, 1, PRIYA, *ALL_TIME)
        prompt = moments_block(for_priya)

    texts = {m["text"] for m in for_priya}
    assert texts.isdisjoint(leo_private)
    assert priya_private <= texts  # her own private moments may help *her* output
    for text in leo_private:  # and none of it leaks into the final prompt string, raw or escaped
        assert text not in prompt and escape(text) not in prompt


def test_prompt_for_both_partners_has_no_private_moments_at_all(seeded_db):
    with session(seeded_db) as conn:
        selected = moments.for_prompt(conn, 1, moments.BOTH, *ALL_TIME)
        everything_private = private_texts(conn, PRIYA) | private_texts(conn, LEO)

    assert selected and all(m["visibility"] == "shared" for m in selected)
    assert {m["text"] for m in selected}.isdisjoint(everything_private)


# --- Structure: nothing bypasses the privacy layer -------------------------------------------

def test_only_the_privacy_layer_reads_the_moments_table():
    app_dir = Path(app.main.__file__).parent
    reads = re.compile(r"\b(FROM|JOIN)\s+moments\b", re.IGNORECASE)
    offenders = [str(p.relative_to(app_dir)) for p in app_dir.rglob("*.py")  # includes app/agents/
                 if p.name != "moments.py" and reads.search(p.read_text())]
    assert offenders == [], f"These modules query moments directly, bypassing app/moments.py: {offenders}"

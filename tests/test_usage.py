"""Usage summaries (app/usage.py), the cost report's projection, and the /usage API."""

import pytest
from fastapi.testclient import TestClient

import app.main
from app import usage
from app.db import session
from app.llm import load_prices
from scripts import cost_report


def call(agent, request, cost, user="Priya", via_chat=0, gateway="MISS", **tokens):
    base = {"input_tokens": 1000, "cached_tokens": 800, "cache_write_tokens": 100, "output_tokens": 200,
            "reasoning_tokens": 50}
    return {"agent_name": agent, "request_id": request, "cost_usd": cost, "user_name": user, "via_chat": via_chat,
            "gateway_cache": gateway, "status": "ok"} | base | tokens


ROWS = [
    call("call_topics", "r1", 0.0004), call("call_topics", "r1", 0.0002),  # one run with a retry
    call("call_topics", "r2", 0.0003, user="Leo"),
    call("router", "r3", 0.00006, via_chat=1), call("ritual_builder", "r3", 0.0002, via_chat=1),
    call("drift", "r4", 0.0, gateway="HIT"),
]


def test_runs_count_requests_and_include_retries():
    ct = next(a for a in usage.summarize(ROWS)["by_agent"] if a["agent"] == "call_topics")
    assert (ct["calls"], ct["runs"]) == (3, 2)
    assert ct["avg_per_run"]["cost_usd"] == pytest.approx(0.00045)  # (0.0004 + 0.0002 + 0.0003) / 2
    assert ct["avg_per_run"]["calls"] == 1.5


def test_features_attribute_chat_requests_to_the_chat():
    features = {f["feature"]: f for f in usage.summarize(ROWS)["by_feature"]}
    assert features["Ask Closer chat"]["calls"] == 2  # router + the agent it routed to
    assert features["Prep for our call"]["calls"] == 3
    assert features["Pulse nudge"]["gateway_hits"] == 1


def test_per_user_totals():
    users = {u["user"]: u for u in usage.summarize(ROWS)["by_user"]}
    assert users["Leo"]["cost_usd"] == pytest.approx(0.0003) and users["Priya"]["calls"] == 5


def test_budget_ceiling_is_a_hard_upper_bound():
    # 50,000 tokens/day x 30 days, all at the output price ($0.50 per 1M)
    assert usage.monthly_ceiling_per_user(load_prices()) == pytest.approx(0.75)


def test_projection_scales_with_assumptions_and_users():
    proj = cost_report.project(usage.summarize(ROWS), {"call_topics": 2.0, "router": 1.0}, [1, 100, 10_000])
    per_user = 2 * 52 / 12 * 0.00045 + 1 * 52 / 12 * 0.00006
    assert proj["cost_per_user_month"] == pytest.approx(per_user, rel=1e-3)
    assert proj["users"][10_000] == pytest.approx(per_user * 10_000, rel=1e-3)
    assert proj["ceiling_users"][100] == pytest.approx(75.0)


def test_committed_sample_has_every_agent():
    rows = cost_report.read_csv(cost_report.SAMPLE_CSV)
    assert {r["agent_name"] for r in rows} >= {"call_topics", "drift", "ritual_builder", "router"}
    assert all(r["gateway_cache"] != "HIT" for r in rows)  # measured with the response cache skipped


def test_usage_api_summarizes_the_couples_calls(seeded_db):
    with session(seeded_db) as conn:
        conn.execute("INSERT INTO llm_calls (user_id, agent_name, request_id, status, input_tokens, cached_tokens, "
                     "output_tokens, cost_usd) VALUES (1, 'call_topics', 'x', 'ok', 2000, 1500, 600, 0.0004)")
    data = TestClient(app.main.app).get("/api/usage", headers={"X-User-Id": "2"}).json()
    assert data["total"]["calls"] == 1 and data["by_user"][0]["user"] == "Priya"
    assert {b["user"] for b in data["budgets"]} == {"Priya", "Leo"}
    assert data["ceiling_per_user_month"] == pytest.approx(0.75)

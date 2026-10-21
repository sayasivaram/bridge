"""Tests for app/llm.py: token counts, cost, logging, and the two safety limits
(per-user daily token budget and per-request step limit)."""

import pytest

from app.config import MODEL, MissingAPIKeyError, Settings
from app.db import session
from app.llm import (
    LLM,
    BudgetExceededError,
    ModelMismatchError,
    RunContext,
    StepLimitError,
    Usage,
    load_prices,
    price,
)
from tests.conftest import FakeClient, fake_response

SETTINGS = Settings(provider="portkey", api_key="test-key", model=MODEL, env_files_loaded=())
PRIYA = 1


def make_llm(db_path, client):
    return LLM(SETTINGS, db_path=db_path, client=client)


def test_cost_uses_all_four_token_types():
    usage = Usage(input_tokens=1000, cached_tokens=600, cache_write_tokens=300, output_tokens=200)

    cost = price(usage, load_prices())

    assert usage.uncached_input_tokens == 100
    assert cost["uncached_input"] == pytest.approx(100 * 0.10 / 1e6)
    assert cost["cached_input"] == pytest.approx(600 * 0.01 / 1e6)
    assert cost["cache_write"] == pytest.approx(300 * 0.125 / 1e6)
    assert cost["output"] == pytest.approx(200 * 0.50 / 1e6)
    assert cost["total"] == pytest.approx(0.0001535)


def test_call_returns_text_tokens_and_cost(seeded_db, fake_client):
    run = RunContext(user_id=PRIYA)

    result = make_llm(seeded_db, fake_client).call(run, "call_topics", "STABLE PREFIX", "variable tail")

    assert result.text == "hello"
    assert (result.usage.input_tokens, result.usage.cached_tokens, result.usage.output_tokens) == (1000, 600, 200)
    assert result.cost["total"] == pytest.approx(0.0001535)
    assert run.summary()["cost_usd"] == pytest.approx(0.0001535)


def test_stable_prefix_goes_first_and_model_is_gpt_6_luna(seeded_db, fake_client):
    make_llm(seeded_db, fake_client).call(
        RunContext(user_id=PRIYA), "call_topics", "STABLE PREFIX", "variable tail", cache_key="couple-1"
    )

    req = fake_client.requests[0]
    assert req["model"] == "gpt-6-luna"
    assert req["instructions"] == "STABLE PREFIX"  # sent before the input -> cacheable
    assert req["input"] == "variable tail"
    assert req["prompt_cache_key"] == "couple-1"


def test_every_call_is_logged(seeded_db, fake_client):
    run = RunContext(user_id=PRIYA)
    make_llm(seeded_db, fake_client).call(run, "call_topics", "a", "b")

    with session(seeded_db) as conn:
        row = dict(conn.execute("SELECT * FROM llm_calls").fetchone())

    assert row["user_id"] == PRIYA
    assert row["agent_name"] == "call_topics"
    assert row["request_id"] == run.request_id
    assert row["model"] == "gpt-6-luna-global"
    assert row["status"] == "ok"
    assert (row["input_tokens"], row["cached_tokens"], row["cache_write_tokens"], row["output_tokens"]) == (
        1000, 600, 300, 200)
    assert row["cost_usd"] == pytest.approx(0.0001535)
    assert row["latency_ms"] is not None


# --- Safety: per-user daily token budget -----------------------------------------------------

def test_budget_blocks_call_before_it_reaches_the_model(seeded_db, fake_client):
    with session(seeded_db) as conn:
        conn.execute("UPDATE users SET daily_token_budget = 3000 WHERE id = ?", (PRIYA,))
    llm = make_llm(seeded_db, fake_client)
    llm.call(RunContext(user_id=PRIYA), "call_topics", "a", "b", max_output_tokens=100)  # uses 1,200

    with pytest.raises(BudgetExceededError, match="resets at midnight UTC"):
        llm.call(RunContext(user_id=PRIYA), "call_topics", "a", "b", max_output_tokens=2000)

    assert len(fake_client.requests) == 1  # the blocked call never reached the model


def test_budget_is_per_user(seeded_db, fake_client):
    with session(seeded_db) as conn:
        conn.execute("UPDATE users SET daily_token_budget = 1250")  # 1,200 used + ~101 estimated > 1,250
    llm = make_llm(seeded_db, fake_client)
    llm.call(RunContext(user_id=1), "call_topics", "a", "b", max_output_tokens=100)

    with pytest.raises(BudgetExceededError):
        llm.call(RunContext(user_id=1), "call_topics", "a", "b", max_output_tokens=100)
    llm.call(RunContext(user_id=2), "call_topics", "a", "b", max_output_tokens=100)  # Leo is unaffected


# --- Safety: step / loop limit ---------------------------------------------------------------

def test_step_limit_stops_a_runaway_loop(seeded_db, fake_client):
    llm = make_llm(seeded_db, fake_client)
    run = RunContext(user_id=PRIYA, max_steps=3)

    with pytest.raises(StepLimitError, match="Stopped after 3 steps"):
        while True:  # an agent stuck in a loop
            llm.call(run, "looping_agent", "a", "b")

    assert len(fake_client.requests) == 3


def test_tool_calls_count_toward_the_step_limit():
    run = RunContext(user_id=PRIYA, max_steps=2)
    run.step("tool: read_moments")
    run.step("tool: read_pulses")

    with pytest.raises(StepLimitError):
        run.step("tool: read_rituals")


# --- Model and key checks --------------------------------------------------------------------

def test_other_model_is_rejected_and_logged(seeded_db):
    client = FakeClient(fake_response(model="gpt-4o-mini"))

    with pytest.raises(ModelMismatchError):
        make_llm(seeded_db, client).call(RunContext(user_id=PRIYA), "call_topics", "a", "b")

    with session(seeded_db) as conn:
        row = conn.execute("SELECT status, error FROM llm_calls").fetchone()
    assert row["status"] == "error" and "gpt-4o-mini" in row["error"]


def test_missing_key_fails_with_clear_message(tmp_path):
    repo = tmp_path / "closer"
    no_key = Settings(provider=None, api_key=None, model=MODEL, env_files_loaded=())

    with pytest.raises(MissingAPIKeyError) as err:
        no_key.require_api_key(repo)

    msg = str(err.value)
    assert str(repo / ".env") in msg and str(tmp_path / ".env") in msg
    assert "PORTKEY_API_KEY" in msg

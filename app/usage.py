"""Token usage and cost, summarized from llm_calls. Shared by the /usage page and
scripts/cost_report.py, so both always report the same numbers.

  * per agent: calls, runs (one user request = one run; a retry is a second call in the same run),
    average tokens and cost per run, prompt-cache share, Portkey response-cache hits
  * per feature: which part of the app spent it (a call belongs to "Ask Bridge chat" when its
    request came through the chat, otherwise to the page that triggered it)
  * per user: calls, tokens and cost, and today's tokens against the daily budget
"""

from __future__ import annotations

import sqlite3
from collections import defaultdict
from typing import Any, Iterable

from app import config
from app.llm import daily_budget, tokens_used_today

AGENT_LABEL = {"call_topics": "Call-Topic Agent", "drift": "Drift Agent (Pulse)", "ritual_builder": "Ritual Builder",
               "router": "Router classifier"}
FEATURE = {"call_topics": "Prep for our call", "drift": "Pulse nudge", "ritual_builder": "Rituals: plan tonight",
           "router": "Ask Bridge chat"}
TOKEN_FIELDS = ("input_tokens", "cached_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens")


def load_rows(conn: sqlite3.Connection, couple_id: int | None = None) -> list[dict]:
    """Every logged model call, with the user's name and whether it came through the chat."""
    sql = """
        SELECT c.*, u.name AS user_name,
               EXISTS (SELECT 1 FROM agent_runs r WHERE r.request_id = c.request_id) AS via_chat
        FROM llm_calls c LEFT JOIN users u ON u.id = c.user_id
    """
    args: tuple = ()
    if couple_id is not None:
        sql += " WHERE u.couple_id = ?"
        args = (couple_id,)
    return [dict(r) for r in conn.execute(sql + " ORDER BY c.id", args)]


def feature_of(row: dict) -> str:
    return "Ask Bridge chat" if row.get("via_chat") else FEATURE.get(row["agent_name"], "Other")


def _blank() -> dict:
    return {"calls": 0, "requests": set(), "cost_usd": 0.0, "gateway_hits": 0, "errors": 0} | {f: 0 for f in TOKEN_FIELDS}


def _add(bucket: dict, r: dict) -> None:
    bucket["calls"] += 1
    bucket["requests"].add(r["request_id"])
    bucket["cost_usd"] += r["cost_usd"] or 0
    bucket["gateway_hits"] += (r.get("gateway_cache") == "HIT")
    bucket["errors"] += (r.get("status") == "error")
    for f in TOKEN_FIELDS:
        bucket[f] += int(r.get(f) or 0)


def _finish(bucket: dict) -> dict:
    runs = len(bucket.pop("requests"))
    out = dict(bucket, runs=runs, cost_usd=round(bucket["cost_usd"], 6))
    out["cached_share"] = round(bucket["cached_tokens"] / bucket["input_tokens"], 3) if bucket["input_tokens"] else 0.0
    per = (lambda x, nd: round(x / runs, nd)) if runs else (lambda x, nd: 0.0)  # no calls yet: zeros, not a crash
    out["avg_per_run"] = {f: per(bucket[f], 1) for f in TOKEN_FIELDS} | {
        "cost_usd": per(bucket["cost_usd"], 6), "calls": per(bucket["calls"], 2)}
    return out


def summarize(rows: Iterable[dict]) -> dict[str, Any]:
    total, agents, features, users = _blank(), defaultdict(_blank), defaultdict(_blank), defaultdict(_blank)
    for r in rows:
        _add(total, r)
        _add(agents[r["agent_name"]], r)
        _add(features[feature_of(r)], r)
        _add(users[r.get("user_name") or "unknown"], r)
    order = lambda d: sorted(d.items(), key=lambda kv: -kv[1]["cost_usd"])  # noqa: E731
    return {
        "total": _finish(total),
        "by_agent": [{"agent": a, "label": AGENT_LABEL.get(a, a)} | _finish(b) for a, b in order(agents)],
        "by_feature": [{"feature": f} | _finish(b) for f, b in order(features)],
        "by_user": [{"user": u} | _finish(b) for u, b in order(users)],
    }


def budget_status(conn: sqlite3.Connection, couple_id: int) -> list[dict]:
    rows = conn.execute("SELECT id, name FROM users WHERE couple_id = ? ORDER BY role", (couple_id,)).fetchall()
    return [{"user": r["name"], "used_today": tokens_used_today(conn, r["id"]),
             "daily_budget": daily_budget(conn, r["id"])} for r in rows]


def monthly_ceiling_per_user(prices: dict[str, float], budget: int = config.DEFAULT_DAILY_TOKEN_BUDGET) -> float:
    """The most one user can cost in a 30-day month if they hit the daily budget every day and
    every token were billed at the most expensive rate (output). A hard upper bound."""
    worst = max(prices["input_per_1m"], prices["cache_write_per_1m"], prices["output_per_1m"])
    return round(budget * 30 * worst / 1_000_000, 4)

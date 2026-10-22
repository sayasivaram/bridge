"""Orchestrator for the home-page chat box: guard in -> router -> agent -> guard out -> log.

One RunContext for the whole request, so the router's classifier call (if any) and the agent's
calls share one request id, one step limit, and one token/cost total. Every request, including
refused ones, is logged to agent_runs with its full trace.
"""

from __future__ import annotations

import json
from typing import Any

from app import guard, router, timeutil
from app.agents import call_topics, drift, ritual_builder
from app.agents.ritual_builder import TIME_LABEL
from app.db import session
from app.llm import LLM, BudgetExceededError, RunContext

CAPABILITIES = ("I can help with three things: prep for your next call, check how you two are doing lately, "
                "or pick a ritual for tonight. Which one?")


def _run_agent(llm: LLM, ctx: RunContext, intent: str, user_id: int, message: str) -> tuple[dict, dict]:
    if intent == "call_topics":
        return call_topics.run(llm, user_id, ctx=ctx), {}
    if intent == "pulse_coach":
        return drift.run(llm, user_id, ctx=ctx), {}
    time, energy, assumed = router.ritual_params(message)
    return ritual_builder.run(llm, user_id, time, energy, ctx=ctx), {"time": time, "energy": energy, "assumed": assumed}


def _reply(intent: str, result: dict, params: dict, partner: str) -> str:
    if intent == "call_topics":
        n = len(result["starters"])
        if not n:
            return "No shared moments from the last 7 days to build on yet. Drop a few in the jar first."
        return f"Here are {n} conversation starters for your next call with {partner}, from this week's shared moments."
    if intent == "pulse_coach":
        return {"nudge": "Here's this week's nudge, based on your check-ins.",
                "steady": result.get("title", "No nudge this week."),
                "support": "Before anything else, this is for you."}[result["kind"]]
    if params["assumed"]:  # say what was assumed once, instead of repeating it
        return (f"Three ideas for tonight. I assumed {' and '.join(params['assumed'])}; say something like "
                f"\"quick, I'm tired\" to change it.")
    return f"Three ideas for tonight ({TIME_LABEL[params['time']]}, {params['energy']} energy)."


def handle(llm: LLM, user_id: int, message: str) -> dict[str, Any]:
    ctx = RunContext(user_id=user_id)
    trace: list[dict] = []
    with session(llm.db_path) as conn:
        user = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
        partner = conn.execute("SELECT name FROM users WHERE couple_id = ? AND id != ?",
                               (user["couple_id"], user_id)).fetchone()
        names = [r[0] for r in conn.execute("SELECT name FROM users WHERE couple_id = ?", (user["couple_id"],))]
    out: dict[str, Any] = {"message": message, "intent": None, "agent": None, "route_method": None,
                           "result": None, "params": {}}

    # 1. Guard (input)
    verdict = guard.check_input(message, user["timezone"])
    trace.append({"stage": "guard_in", "detail": verdict.detail})
    if not verdict.ok:
        status = "support" if verdict.kind == "crisis" else "blocked"
        out |= {"status": status, "route_method": "guard", "reply": verdict.reply, "support": verdict.support}
        return _finish(llm, ctx, user_id, out, trace)

    # 2. Router
    r = router.route(llm, ctx, message)
    trace.append({"stage": "router", "detail": r.detail, "scores": r.scores})
    out |= {"intent": r.intent, "route_method": r.method}
    if r.intent is None:
        out |= {"status": "clarify", "reply": CAPABILITIES}
        return _finish(llm, ctx, user_id, out, trace)
    out["agent"] = router.AGENT_LABEL[r.intent]

    # 3. Agent
    try:
        result, params = _run_agent(llm, ctx, r.intent, user_id, message)
    except BudgetExceededError as err:
        trace.append({"stage": "agent", "detail": f"{out['agent']}: refused by the daily token budget"})
        out |= {"status": "error", "reply": str(err)}
        return _finish(llm, ctx, user_id, out, trace)
    except Exception as err:
        print(f"chat agent failed: {type(err).__name__}: {err}")
        trace.append({"stage": "agent", "detail": f"{out['agent']} failed: {type(err).__name__}"})
        out |= {"status": "error", "reply": "Something went wrong on my side. Please try again in a moment."}
        return _finish(llm, ctx, user_id, out, trace)
    for line in result.get("trace", []):
        trace.append({"stage": "agent", "detail": f"{out['agent']}: {line.strip()}"})

    # 4. Guard (output)
    clean, notes = guard.check_output(r.intent, result, names)
    trace += [{"stage": "guard_out", "detail": n} for n in notes]
    if clean is None:
        out |= {"status": "error", "reply": "That answer didn't pass my checks, so I won't show it. Please try again."}
        return _finish(llm, ctx, user_id, out, trace)
    out |= {"status": "ok", "result": clean, "params": params,
            "reply": _reply(r.intent, clean, params, partner["name"] if partner else "your partner")}
    return _finish(llm, ctx, user_id, out, trace)


def _finish(llm: LLM, ctx: RunContext, user_id: int, out: dict, trace: list[dict]) -> dict:
    usage = ctx.summary()
    trace.append({"stage": "done", "detail": f"status {out['status']}; {usage['calls']} model call(s), "
                                             f"{usage['input_tokens']:,} in ({usage['cached_tokens']:,} cached), "
                                             f"{usage['output_tokens']:,} out, ${usage['cost_usd']:.5f}"})
    with session(llm.db_path) as conn:
        run_id = conn.execute(
            "INSERT INTO agent_runs (created_at, user_id, message, status, intent, agent, route_method, request_id, "
            "model_calls, input_tokens, cached_tokens, output_tokens, cost_usd, trace) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (timeutil.to_stored(timeutil.now_utc()), user_id, out["message"][:2000], out["status"], out["intent"],
             out["agent"], out["route_method"], ctx.request_id, usage["calls"], usage["input_tokens"],
             usage["cached_tokens"], usage["output_tokens"], usage["cost_usd"], json.dumps(trace)),
        ).lastrowid
    return out | {"run_id": run_id, "request_id": ctx.request_id, "usage": usage, "trace": trace}


def runs_for_viewer(conn, viewer_id: int, limit: int = 10) -> list[dict]:
    """The viewer's own recent runs (never the partner's), newest first."""
    rows = conn.execute("SELECT * FROM agent_runs WHERE user_id = ? ORDER BY id DESC LIMIT ?",
                        (viewer_id, limit)).fetchall()
    return [dict(r) | {"trace": json.loads(r["trace"])} for r in rows]

"""Drift Detector: this week's nudge on the Pulse page.

Layer 1, rules (plain Python, no model, $0): signals over the last 4 weeks
  * each partner's connection-score trend, the gap between partners,
  * the "amount of contact" answers, shared moments logged per week,
  * days since each partner's last check-in, plus how recent rituals were rated.
  Only data BOTH partners can see goes in (weeks both submitted, shared moments and shared
  notes), because the nudge is shown to both.

Layer 2, agent (gpt-6-luna), called ONLY when a rule flags drift. It gets the computed signals
and shared notes, and returns one gentle, specific suggestion plus a short explanation. Output
is checked in code (coach boundary, numbers must come from the signals, no private text, cited
signals must be real flags); one retry, then a rule-based fallback. The nudge is cached per
couple and signals, so both partners see the same one and the model isn't called again.

Before either layer: the crisis pre-check (app/safety.py) on every recent note, private ones
included. If it matches, there's no coaching that week; the author gets support resources and
the other partner sees the same "nothing to flag" card as a quiet week.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from datetime import date, datetime, timedelta
from typing import Any

from app import moments, pulses, safety, timeutil
from app.agents.call_topics import _leaks, _shingles, couple_profile_block, private_fingerprints
from app.db import session
from app.llm import LLM, BudgetExceededError, RunContext
from app.prompting import UNTRUSTED_DATA_RULES, escape, wrap

AGENT_NAME = "drift"
WINDOW_WEEKS = 4
REASONING_EFFORT = "low"
MAX_OUTPUT_TOKENS = 1_200
MAX_RETRIES = 1

# Rule thresholds (tuned on the seed couple; see tests/test_pulse.py)
FALL_BY = 2  # score dropped this much from first to last check-in in the window
LOW_SCORE = 2  # latest score at or below this
GAP = 2  # partners' latest scores this far apart
MOMENTS_DROP = 0.5  # last completed week below half the earlier weekly average...
MOMENTS_MIN_AVG = 3  # ...when that average was at least this
MISSED_DAYS = 10  # no check-in for this many days

# Shown for a quiet week AND to the other partner when a crisis note paused coaching, so it must
# be true in both cases and give nothing away.
QUIET = {"kind": "steady", "title": "No nudge this week",
         "body": "Nothing to suggest right now. Keep checking in; a nudge will show up when there's "
                 "something worth trying together."}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["suggestion", "explanation", "signals_used"],
    "properties": {
        "suggestion": {"type": "string"},
        "explanation": {"type": "string"},
        "signals_used": {"type": "array", "items": {"type": "string"}},
    },
}

SYSTEM_PROMPT = """\
You are the Drift Agent in Closer, an app that helps long-distance couples stay close. Each week
both partners do a 30-second check-in. Simple rules have already looked at the last few weeks
and flagged some drift (for example falling connection scores, contact that feels too little,
or fewer moments shared). Your job is to turn those flags into ONE small, specific, gentle thing
the couple can do together this week.

## Rules
1. One suggestion only: a concrete action for this week, doable in a long-distance relationship,
   in at most two sentences. Prefer something they already enjoyed (see recent rituals) or a
   tiny change to how they already connect. No lists, no multi-step plans.
2. Write to both partners together ("you two", "the two of you"). The same text is shown to
   both of them. Never take sides, assign blame, or imply one partner is doing it wrong.
3. You are not a therapist and not a judge of the relationship. Never diagnose anyone, use
   clinical or labelling words (depressed, anxious, toxic, codependent, red flag...), predict
   the relationship's future, or suggest breaking up, taking a break, or leaving.
4. The explanation (at most two sentences) says which signals led to the suggestion, in warm,
   neutral words. Use only facts and numbers that appear in <signals>. Never invent events,
   reasons, or feelings; you don't know why the signals moved, so don't guess.
5. signals_used lists the flag keys (like "contact_too_little") your suggestion responds to.
   Use only keys listed under Flags in <signals>.
6. Shared notes are things a partner chose to share. You may reflect their spirit gently;
   don't quote them at length. You never see private notes, so never refer to anything private.

""" + UNTRUSTED_DATA_RULES + """
## Output
JSON matching the schema: suggestion, explanation, signals_used.

## Example (a different couple; don't reuse its wording)
Flags: contact_too_little (both), fewer_moments (Sam). Recent rituals: "Board game night" rated 5/5.
Good: suggestion "Put one board game night on the calendar for this weekend, phones down, no
catching up on logistics first." explanation "You both said contact felt like too little last
week, and board game night is the ritual you rated highest." signals_used ["contact_too_little"].
Bad: "Sam seems to be pulling away; Ana should give him space." Takes sides and guesses at feelings.
Bad: "You might be drifting apart for good." Predicts the future.
"""

_BOUNDARY = [
    r"\bbreak(?:ing)? up\b", r"\bbreakup\b", r"\bdivorc", r"\btake a break\b", r"\bleave (?:him|her|them|each other)\b",
    r"\bend (?:the|your|this) relationship\b", r"\btoxic\b", r"\bnarcissis", r"\bgaslight", r"\bcodependen",
    r"\bred flags?\b", r"\bdiagnos", r"\bdisorder\b", r"\bdepress(?:ed|ion)\b", r"\banxious attachment\b",
    r"\b(?:won'?t|will not|isn'?t going to|is not going to) (?:last|work out)\b", r"\bdoomed\b",
    r"\bdrifting apart for good\b", r"\byour relationship (?:will|is going to)\b", r"\bto blame\b",
    r"\b(?:his|her|their) fault\b", r"\bis the problem\b", r"\bpulling away\b",
]


def user_facing(problem: str) -> str:
    """Trace text users can see: say THAT a coach-boundary phrase was blocked, never quote it (the
    exact words still go to the model as retry feedback, and to the server log)."""
    if problem.startswith("Crossed the coach boundary"):
        print(f"drift: blocked output: {problem}", file=sys.stderr)
        return "Crossed the coach boundary (wording withheld)."
    return problem


def boundary_violations(text: str, names: list[str]) -> list[str]:
    patterns = _BOUNDARY + [rf"\b{re.escape(n)}'?s fault\b" for n in names]
    return [m.group(0) for p in patterns for m in [re.search(p, text, re.IGNORECASE)] if m]


# --- Layer 1: rules --------------------------------------------------------------------------

def compute_signals(conn, couple_id: int, this_week: date) -> dict[str, Any]:
    weeks = [this_week - timedelta(weeks=k) for k in range(WINDOW_WEEKS - 1, -1, -1)]
    partners = [dict(r) for r in conn.execute(
        "SELECT id, name, timezone FROM users WHERE couple_id = ? ORDER BY role", (couple_id,))]
    names = {p["id"]: p["name"] for p in partners}
    revealed = pulses.revealed_for_couple(conn, couple_id, weeks)
    counts = moments.weekly_shared_counts(conn, couple_id, weeks)
    last = pulses.last_checkin(conn, couple_id)
    now = timeutil.now_utc()
    rituals = [dict(r) for r in conn.execute(
        "SELECT r.title, r.status, r.done_on, ROUND(AVG(f.rating), 1) AS avg_rating, COUNT(f.id) AS ratings "
        "FROM rituals r LEFT JOIN ritual_feedback f ON f.ritual_id = r.id WHERE r.couple_id = ? "
        "GROUP BY r.id ORDER BY r.created_at DESC LIMIT 6", (couple_id,))]

    flags: list[dict] = []
    per: dict[str, dict] = {}
    for p in partners:
        scores = [w["by_user"][p["id"]]["score"] for w in revealed if p["id"] in w["by_user"]]
        completed = counts[p["id"]][:-1]  # this week isn't over yet
        days = None
        if p["id"] in last:
            days = (now - datetime.strptime(last[p["id"]], timeutil.STORED).replace(tzinfo=timeutil.UTC)).days
        per[p["name"]] = {"scores": scores, "moments_per_week": completed, "moments_this_week": counts[p["id"]][-1],
                          "days_since_checkin": days}
        if len(scores) >= 2 and scores[0] - scores[-1] >= FALL_BY:
            flags.append({"key": "score_falling", "who": p["name"],
                          "detail": f"{p['name']}'s connection score went {' → '.join(map(str, scores))}."})
        elif scores and scores[-1] <= LOW_SCORE:
            flags.append({"key": "score_low", "who": p["name"],
                          "detail": f"{p['name']}'s latest connection score was {scores[-1]} out of 5."})
        earlier = completed[:-1]
        if earlier and sum(earlier) / len(earlier) >= MOMENTS_MIN_AVG and completed[-1] <= MOMENTS_DROP * sum(earlier) / len(earlier):
            flags.append({"key": "fewer_moments", "who": p["name"],
                          "detail": f"{p['name']} shared {completed[-1]} moment{'s' * (completed[-1] != 1)} last week, "
                                    f"down from {', '.join(map(str, earlier))} in the weeks before."})
        if days is None or days >= MISSED_DAYS:
            flags.append({"key": "missed_checkins", "who": p["name"],
                          "detail": f"{p['name']} hasn't checked in for {days} days." if days is not None
                          else f"{p['name']} hasn't checked in yet."})

    if revealed:
        latest = revealed[-1]["by_user"]
        a, b = (latest.get(p["id"]) for p in partners)
        if a and b:
            if abs(a["score"] - b["score"]) >= GAP:
                flags.append({"key": "score_gap", "who": "both",
                              "detail": f"Latest scores were {a['score']} and {b['score']}, {abs(a['score'] - b['score'])} apart."})
            contacts = {names[uid]: v["contact"] for uid, v in latest.items()}
            if set(contacts.values()) == {"too_little", "too_much"}:
                flags.append({"key": "contact_mismatch", "who": "both",
                              "detail": "One of you wanted more contact last week and the other wanted less."})
            elif "too_little" in contacts.values():
                who = [n for n, c in contacts.items() if c == "too_little"]
                flags.append({"key": "contact_too_little", "who": "both" if len(who) == 2 else who[0],
                              "detail": ("You both said" if len(who) == 2 else f"{who[0]} said")
                                        + " the amount of contact felt like too little in the latest check-in."})

    shared_notes = [{"author": names[uid], "week_start": w["week_start"], "note": v["shared_note"]}
                    for w in revealed for uid, v in w["by_user"].items() if v["shared_note"]]
    return {"weeks": [w.isoformat() for w in weeks], "weeks_both_checked_in": len(revealed),
            "partners": per, "rituals": rituals, "flags": flags, "shared_notes": shared_notes}


def signals_text(sig: dict) -> str:
    """The signals as plain lines for the prompt. Built by our code; titles are escaped."""
    lines = [f"Weeks in view: {', '.join(sig['weeks'])} (the last one is this week, still in progress).",
             f"Weeks both partners checked in: {sig['weeks_both_checked_in']}."]
    for name, p in sig["partners"].items():
        lines.append(f"{name}: connection scores {', '.join(map(str, p['scores'])) or 'none yet'}; "
                     f"shared moments per completed week {', '.join(map(str, p['moments_per_week']))}, "
                     f"{p['moments_this_week']} so far this week; days since last check-in "
                     f"{p['days_since_checkin'] if p['days_since_checkin'] is not None else 'never'}.")
    for r in sig["rituals"]:
        rating = f", rated {r['avg_rating']}/5 on average" if r["ratings"] else ""
        lines.append(f'Recent ritual: "{escape(r["title"])}" ({r["status"]}{rating}).')
    lines.append("Flags:")
    lines += [f"- {f['key']} ({f['who']}): {f['detail']}" for f in sig["flags"]]
    return "<signals>\n" + "\n".join(lines) + "\n</signals>"


def signals_hash(sig: dict) -> str:
    """Cache key for the nudge. Leaves out 'days since last check-in', which changes daily on its
    own (a missed check-in still changes the key, through its flag)."""
    stable = {**sig, "partners": {n: {k: v for k, v in p.items() if k != "days_since_checkin"}
                                  for n, p in sig["partners"].items()}}
    return hashlib.sha256(json.dumps(stable, sort_keys=True, default=str).encode()).hexdigest()[:16]


# --- Layer 2: agent --------------------------------------------------------------------------

def validate(answer: Any, sig: dict, sig_text: str, fingerprints: set[str]) -> tuple[dict | None, list[str]]:
    if not isinstance(answer, dict):
        return None, ["The answer was not a JSON object."]
    suggestion = str(answer.get("suggestion") or "").strip()
    explanation = str(answer.get("explanation") or "").strip()
    used = answer.get("signals_used") or []
    problems = []
    if not suggestion or len(suggestion) > 400:
        problems.append("The suggestion must be one or two sentences.")
    if not explanation or len(explanation) > 400:
        problems.append("The explanation must be one or two sentences.")
    keys = {f["key"] for f in sig["flags"]}
    if not used or not isinstance(used, list) or not set(map(str, used)) <= keys:
        problems.append(f"signals_used must only list these flag keys: {', '.join(sorted(keys))}.")
    names = list(sig["partners"])
    bad = boundary_violations(f"{suggestion} {explanation}", names)
    if bad:
        problems.append(f"Crossed the coach boundary with: {', '.join(bad)}. No diagnosing, predicting, or taking sides.")
    allowed_numbers = set(re.findall(r"\d+(?:\.\d+)?", sig_text))
    invented = [n for n in re.findall(r"\d+(?:\.\d+)?", explanation) if n not in allowed_numbers]
    if invented:
        problems.append(f"The explanation uses numbers that aren't in <signals>: {', '.join(invented)}.")
    if _leaks([suggestion, explanation], fingerprints):
        problems.append("The text repeats private content.")
    if problems:
        return None, problems
    return {"suggestion": suggestion, "explanation": explanation, "signals_used": list(map(str, used))}, []


_PRIORITY = ["contact_mismatch", "contact_too_little", "score_falling", "score_gap", "fewer_moments", "score_low",
             "missed_checkins"]


def fallback_nudge(sig: dict) -> dict:
    """Rule-based nudge when the agent can't produce a valid one. Safe by construction."""
    top = min(sig["flags"], key=lambda f: _PRIORITY.index(f["key"]))
    best = max((r for r in sig["rituals"] if r["ratings"] and r["status"] == "done"),
               key=lambda r: r["avg_rating"], default=None)
    ritual = f' Maybe bring back "{best["title"]}", which you rated {best["avg_rating"]}/5.' if best and best["avg_rating"] >= 4 else ""
    suggestion = {
        "contact_mismatch": "Agree on one rhythm for this week together: when you'll talk, and for how long.",
        "contact_too_little": "Add one short, no-agenda call this week, just to hang out." + ritual,
        "score_falling": "Plan one easy, low-pressure thing to do together this week." + ritual,
        "score_gap": "On your next call, each share one moment from the week that made you feel close.",
        "fewer_moments": "Try dropping one small moment a day into the jar this week, even a tiny one.",
        "score_low": "Plan one easy, low-pressure thing to do together this week." + ritual,
        "missed_checkins": "Do this week's 30-second check-in together at the start of your next call.",
    }[top["key"]]
    return {"suggestion": suggestion, "explanation": top["detail"], "signals_used": [top["key"]]}


def _cached(conn, couple_id: int, h: str) -> dict | None:
    row = conn.execute("SELECT * FROM nudges WHERE couple_id = ? AND signals_hash = ?", (couple_id, h)).fetchone()
    if not row:
        return None
    cost = conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls WHERE request_id = ?",
                        (row["request_id"],)).fetchone()[0] if row["request_id"] else 0
    return {"suggestion": row["suggestion"], "explanation": row["explanation"], "source": row["source"],
            "first_cost_usd": round(cost, 8)}


def run(llm: LLM, viewer_id: int, ctx: RunContext | None = None) -> dict[str, Any]:
    """This week's nudge for the viewer. Calls the model only if rules flag drift and nothing is cached."""
    ctx = ctx or RunContext(user_id=viewer_id)  # the orchestrator passes its own to share the request id
    trace: list[str] = []
    with session(llm.db_path) as conn:
        viewer = conn.execute("SELECT id, couple_id, name, timezone FROM users WHERE id = ?", (viewer_id,)).fetchone()
        couple_id = viewer["couple_id"]
        this_week = pulses.current_week(conn, viewer_id)
        window_start = this_week - timedelta(weeks=WINDOW_WEEKS - 1)

        # 0. Crisis pre-check on every recent note (code only; nothing is sent anywhere).
        flagged = [(n, safety.precheck(n["note"])) for n in pulses.notes_for_safety_check(conn, couple_id, window_start)]
        flagged = [(n, f) for n, f in flagged if f]
        mine = [f for n, f in flagged if n["user_id"] == viewer_id]
        if mine:
            urgent = min(mine, key=lambda f: ["self_harm", "abuse", "distress"].index(f.category))
            return {"kind": "support", "support": safety.support_message(urgent, viewer["timezone"]),
                    "usage": ctx.summary()}
        if flagged:  # the other partner's note: no coaching, and nothing that hints at why
            return QUIET | {"flags": [], "trace": [], "usage": ctx.summary()}

        # 1. Rules.
        sig = compute_signals(conn, couple_id, this_week)
        trace.append(f"Rules checked {WINDOW_WEEKS} weeks ({sig['weeks_both_checked_in']} with both check-ins): "
                     f"{len(sig['flags'])} flag(s){': ' + ', '.join(f['key'] for f in sig['flags']) if sig['flags'] else ''}. ($0, no model)")
        if not sig["flags"]:
            trace.append("No drift, so the agent wasn't called.")
            return QUIET | {"flags": [], "trace": trace, "usage": ctx.summary()}

        h = signals_hash(sig)
        cached = _cached(conn, couple_id, h)
        if cached:
            trace.append(f"Same signals as an earlier nudge this week: reused it ($0 now; it first cost "
                         f"${cached['first_cost_usd']:.5f}).")
            return {"kind": "nudge", **cached, "cached": True, "flags": sig["flags"], "trace": trace,
                    "usage": ctx.summary()}
        sig_text = signals_text(sig)
        notes = "\n".join(wrap("shared_note", n["note"], author=n["author"], week=n["week_start"])
                          for n in sig["shared_notes"]) or "(no shared notes)"
        fingerprints = private_fingerprints(conn, couple_id, [n["note"] for n in sig["shared_notes"]])
        instructions = f"{SYSTEM_PROMPT}\n## This couple\n{couple_profile_block(conn, couple_id)}\n"

    # 2. Agent (only reached when rules flagged drift).
    user_input = (f"{wrap('request', 'Write this week’s nudge for both partners.')}\n{sig_text}\n"
                  f"<shared_notes>\n{notes}\n</shared_notes>")
    nudge, source = None, "llm"
    try:
        for attempt in range(1 + MAX_RETRIES):
            r = llm.call(ctx, AGENT_NAME, instructions, user_input, max_output_tokens=MAX_OUTPUT_TOKENS,
                         cache_key=f"closer-{AGENT_NAME}-couple-{couple_id}", json_schema=("drift_nudge", SCHEMA),
                         reasoning_effort=REASONING_EFFORT)
            try:
                answer = json.loads(r.text)
            except json.JSONDecodeError:
                answer = None
            nudge, problems = validate(answer, sig, sig_text, fingerprints)
            trace.append(f"{'Agent' if attempt == 0 else 'Retry'}: {r.usage.input_tokens:,} tokens in "
                         f"({r.usage.cached_tokens:,} cached), {r.usage.output_tokens:,} out. "
                         + ("Passed checks." if nudge else "Rejected: " + " ".join(map(user_facing, problems))))
            if nudge:
                break
            user_input += ("\n<validation_feedback>\n" + "\n".join(f"- {escape(p)}" for p in problems)
                           + "\nWrite a new answer that follows every rule.\n</validation_feedback>")
    except BudgetExceededError:
        trace.append("AI budget for today is used up, so this nudge comes from the rules alone.")
        return {"kind": "nudge", **fallback_nudge(sig), "source": "fallback", "flags": sig["flags"], "trace": trace,
                "usage": ctx.summary()}
    except Exception as err:  # API trouble: still give a safe nudge
        print(f"drift agent failed: {type(err).__name__}: {err}")
        trace.append("The AI service didn't answer, so this nudge comes from the rules alone.")
        return {"kind": "nudge", **fallback_nudge(sig), "source": "fallback", "flags": sig["flags"], "trace": trace,
                "usage": ctx.summary()}

    if nudge is None:
        nudge, source = fallback_nudge(sig), "fallback"
        trace.append("Both answers failed the checks, so this nudge comes from the rules alone (max 1 retry).")
    with session(llm.db_path) as conn:
        conn.execute("INSERT OR IGNORE INTO nudges (couple_id, signals_hash, source, suggestion, explanation, request_id) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (couple_id, h, source, nudge["suggestion"], nudge["explanation"],
                                                    ctx.request_id))
    return {"kind": "nudge", **nudge, "source": source, "cached": False, "flags": sig["flags"], "trace": trace,
            "usage": ctx.summary(), "model": ctx.results[-1].model if ctx.results else None}

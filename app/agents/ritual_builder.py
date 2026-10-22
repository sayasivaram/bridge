"""Ritual Builder: 3 rituals for tonight, picked from the catalog and personalized.

  1. Code filters the catalog to rituals that fit tonight's time and energy (allowed ids).
  2. The feedback loop: the couple's highest- and lowest-rated past rituals (with unflagged notes)
     go into the prompt, plus what they recently planned or skipped.
  3. gpt-6-luna picks 3 allowed ids and writes why each fits, a concrete first step, and an
     optional light variation. The whole catalog sits in the stable, cacheable prompt prefix.
  4. Validation in code: every id must exist in the catalog and fit tonight; no duplicates; no
     invented bookings or purchases; numbers in "why" must come from the history; no private
     text. One retry with feedback; any remaining gaps are filled by a rule-based pick.
"""

from __future__ import annotations

import json
import re
from datetime import date
from typing import Any

from app import rituals, timeutil
from app.agents.call_topics import _leaks, couple_profile_block, private_fingerprints
from app.agents.drift import boundary_violations
from app.db import session
from app.llm import LLM, RunContext
from app.prompting import UNTRUSTED_DATA_RULES, escape, wrap

AGENT_NAME = "ritual_builder"
N_PICKS = 3
REASONING_EFFORT = "low"
MAX_OUTPUT_TOKENS = 1_500
MAX_RETRIES = 1
TIME_LABEL = {"15": "about 15 minutes", "60": "about an hour", "120": "two hours or more"}

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["picks"],
    "properties": {"picks": {"type": "array", "items": {
        "type": "object",
        "additionalProperties": False,
        "required": ["ritual_id", "why", "first_step", "variation"],
        "properties": {"ritual_id": {"type": "string"}, "why": {"type": "string"},
                       "first_step": {"type": "string"}, "variation": {"type": "string"}},
    }}},
}


def catalog_block() -> str:
    lines = [f"- {r['id']} | {escape(r['name'])} | {r['category']} | {r['minutes']} min | {r['energy']} energy | "
             f"cost: {r['cost']} | setup: {escape(r['setup'])} | {escape(r['description'])}"
             for r in rituals.catalog().values()]
    return "<ritual_catalog>\n" + "\n".join(lines) + "\n</ritual_catalog>"


SYSTEM_PROMPT = f"""\
You are the Ritual Builder in Bridge, an app that helps long-distance couples stay close. A
partner tells you how much time and energy they have tonight. You pick {N_PICKS} shared rituals
from Bridge's catalog and make each one feel like it was chosen for this couple.

## Rules
1. Pick exactly {N_PICKS} different rituals, by ritual_id, ONLY from <allowed_ids>. Those are the
   catalog rituals that fit tonight's time and energy. Never invent a ritual or an id. Prefer
   {N_PICKS} different categories.
2. Learn from their history. Lean toward what they rated highly (the same ritual with a fresh
   twist, or one with the same feel). Steer away from low-rated rituals and from whatever their
   notes say didn't work. Don't suggest a ritual they rated low.
3. why: one or two sentences on why it fits THIS couple tonight: a past rating or note, their
   time zones, interests, or an inside joke from the profile. Use only facts from the data; any
   rating you mention must match the history exactly. Never guess at feelings.
4. first_step: one concrete thing they can do in the next five minutes to start, using only what
   the catalog's setup line says the ritual needs.
5. variation: an optional light personal twist (a recipe or show from their history, an inside
   joke), or "" if none. A variation must not add a purchase, booking, ticket, subscription,
   delivery, takeout, or travel. A free ritual stays free.
6. Warm and short. Write to both partners ("you two"). No advice about the relationship itself.

{UNTRUSTED_DATA_RULES}
## Output
JSON matching the schema: picks, each with ritual_id, why, first_step, variation.

## Example (a different couple; don't reuse its wording)
History: highest "Same-recipe cook-along" (variation "Ramen night") 5/5, note "Loved it";
lowest "Draw-and-guess" 2/5, note "Too chaotic after work". Tonight: an hour, low energy.
Good pick: ritual_id "video-breakfast", why "Your ramen cook-along was a 5/5, and this keeps the
eat-together feel without the cooking effort tonight.", first_step "Each grab whatever's in the
fridge and call when you sit down.", variation "Ramen leftovers count."
Bad pick: "Book a table at the same restaurant chain in both cities." Needs a booking.
Bad pick: ritual_id "draw-and-guess". They rated it 2/5 and called it too chaotic.

## The catalog
{catalog_block()}
"""

_BOOKING = [r"\bbook (?:a|an|tickets?|seats?|tables?|reservations?|a class)\b", r"\bbooking\b", r"\breserv(?:e|ation)",
            r"\btickets?\b", r"\bsubscri", r"\bdelivery\b", r"\btake-?out\b", r"\bflights?\b", r"\bhotel",
            r"\bairbnb\b", r"\border (?:food|in|online|takeout)\b", r"\bsign up\b"]
_PURCHASE = [r"\bbuy(?:ing)?\b", r"\bbought\b", r"\bpurchas", r"\bshop(?:ping)? for\b", r"\$\d", r"£\d"]


def spending_problems(text: str, item: dict) -> list[str]:
    """Bookings are never OK; purchases only if the catalog ritual itself costs something."""
    patterns = _BOOKING + (_PURCHASE if item["cost"] == "free" else [])
    return [m.group(0) for p in patterns for m in [re.search(p, text, re.IGNORECASE)] if m]


def history_block(h: dict) -> str:
    def line(r: dict) -> str:
        twist = f' (variation "{escape(r["variation"])}")' if r.get("variation") else ""
        return f'- {r["catalog_id"] or "custom"}: "{escape(r["title"])}"{twist}'

    def section(title: str, rows: list[dict], fmt) -> list[str]:
        return [title] + ([fmt(r) for r in rows] if rows else ["- none yet"])

    parts = (section("Highest rated:", h["highest"], lambda r: f"{line(r)}, average {r['avg_rating']}/5")
             + section("Lowest rated:", h["lowest"], lambda r: f"{line(r)}, average {r['avg_rating']}/5")
             + section("Recently planned or skipped:", h["recent"], lambda r: f"{line(r)}, {r['status']}"))
    notes = [wrap("feedback", r["feedback"], ritual=r["title"]) for r in h["highest"] + h["lowest"] if r["feedback"]]
    return "<history>\n" + "\n".join(parts) + "\n</history>\n<feedback_notes>\n" + (
        "\n".join(notes) or "(none)") + "\n</feedback_notes>"


def build_prompt(conn, requester_id: int, time: str, energy: str) -> dict[str, Any]:
    user = conn.execute("SELECT * FROM users WHERE id = ?", (requester_id,)).fetchone()
    allowed = rituals.candidates(time, energy)
    h = rituals.history_for_prompt(conn, user["couple_id"])
    today = timeutil.local_today(user["timezone"])
    request = wrap("request", f"{user['name']} is asking for tonight ({today:%A}). Time available: {TIME_LABEL[time]}. "
                              f"Energy: {energy}.")
    hist = history_block(h)
    return {
        "instructions": f"{SYSTEM_PROMPT}\n## This couple\n{couple_profile_block(conn, user['couple_id'])}\n",
        "user_input": f"{request}\n<allowed_ids>{', '.join(r['id'] for r in allowed)}</allowed_ids>\n{hist}",
        "allowed": {r["id"] for r in allowed},
        "history": h,
        "history_text": hist,
        "user": dict(user),
    }


def validate(answer: Any, allowed: set[str], history_text: str, fingerprints: set[str],
             names: list[str]) -> tuple[list[dict], list[str]]:
    if not isinstance(answer, dict) or not isinstance(answer.get("picks"), list):
        return [], ["The answer was not JSON with a picks list."]
    cat, good, problems, seen = rituals.catalog(), [], [], set()
    # Ratings mentioned in "why" ("4/5", "rated 4.5") must be real ones from the history. Other
    # numbers (minutes, time differences) are fine.
    ratings_ok = {float(x) for x in re.findall(r"(\d(?:\.\d)?)/5", history_text)}
    for n, p in enumerate(answer["picks"], 1):
        rid = str(p.get("ritual_id", "")) if isinstance(p, dict) else ""
        label = f"Pick {n} ({rid or 'no id'})"
        if rid not in cat:
            problems.append(f"{label}: that id is not in the catalog.")
            continue
        if rid not in allowed:
            problems.append(f"{label}: doesn't fit tonight's time and energy; use only <allowed_ids>.")
            continue
        if rid in seen:
            problems.append(f"{label}: already picked.")
            continue
        why, step, var = (str(p.get(k) or "").strip() for k in ("why", "first_step", "variation"))
        if not why or not step or len(why) > 400 or len(step) > 300 or len(var) > 200:
            problems.append(f"{label}: why and first_step must each be one or two sentences.")
            continue
        text = f"{why} {step} {var}"
        spend = spending_problems(f"{step} {var}", cat[rid])
        if spend:
            problems.append(f"{label}: adds a purchase or booking ({', '.join(spend)}); the catalog ritual doesn't need one.")
            continue
        claimed = re.findall(r"(\d(?:\.\d)?)\s*(?:/|out of)\s*5\b", why) + re.findall(r"\brated (?:it |them )?(\d(?:\.\d)?)\b", why)
        bad_ratings = [x for x in claimed if float(x) not in ratings_ok]
        if bad_ratings:
            problems.append(f"{label}: 'why' mentions a rating that isn't in the history: {', '.join(bad_ratings)}.")
            continue
        if boundary_violations(text, names) or _leaks([text], fingerprints):
            problems.append(f"{label}: crosses the coach boundary or repeats private content.")
            continue
        seen.add(rid)
        good.append({"ritual_id": rid, "why": why, "first_step": step, "variation": var})
    if len(good) < N_PICKS and not problems:
        problems.append(f"Only {len(good)} valid picks; {N_PICKS} are needed.")
    return good, problems


def rule_picks(allowed: set[str], history: dict, exclude: set[str], k: int) -> list[dict]:
    """Fallback: score allowed rituals by the couple's ratings (same ritual or category as their
    favorites up, their least favorites down), keep categories varied."""
    cat = rituals.catalog()
    high = {r["catalog_id"]: r for r in history["highest"] if r["catalog_id"]}
    low = {r["catalog_id"]: r for r in history["lowest"] if r["catalog_id"] and r["avg_rating"] < 3.5}
    high_cats = {cat[c]["category"] for c in high if c in cat}
    low_cats = {cat[c]["category"] for c in low if c in cat}

    def score(rid: str) -> float:
        c = cat[rid]["category"]
        return 3 * (rid in high) + (c in high_cats) - 5 * (rid in low) - (c in low_cats)

    ranked = sorted((r for r in allowed if r not in exclude and r not in low), key=lambda r: (-score(r), r))
    picks, used_cats = [], set()
    for rid in ranked:  # first pass: one per category
        if len(picks) < k and cat[rid]["category"] not in used_cats:
            picks.append(rid)
            used_cats.add(cat[rid]["category"])
    picks += [r for r in ranked if r not in picks][:k - len(picks)]
    out = []
    for rid in picks:
        item = cat[rid]
        why = f"Fits tonight: about {item['minutes']} minutes, {item['energy']} energy."
        if rid in high:
            why += f" You rated it {high[rid]['avg_rating']}/5 last time."
        elif item["category"] in high_cats:
            fav = next(r for c, r in high.items() if cat.get(c, {}).get("category") == item["category"])
            why += f" Same feel as \"{fav['title']}\", which you rated {fav['avg_rating']}/5."
        out.append({"ritual_id": rid, "why": why, "first_step": item["setup"], "variation": ""})
    return out


def run(llm: LLM, requester_id: int, time: str, energy: str, ctx: RunContext | None = None) -> dict[str, Any]:
    """ctx: pass the orchestrator's context to share its request id and step limit."""
    ctx = ctx or RunContext(user_id=requester_id)
    trace: list[str] = []
    with session(llm.db_path) as conn:
        p = build_prompt(conn, requester_id, time, energy)
        couple_id = p["user"]["couple_id"]
        names = [r[0] for r in conn.execute("SELECT name FROM users WHERE couple_id = ?", (couple_id,))]
        fingerprints = private_fingerprints(conn, couple_id, [])
    h = p["history"]
    trace.append(f"{len(p['allowed'])} of {len(rituals.catalog())} catalog rituals fit {TIME_LABEL[time]} at {energy} "
                 f"energy (filtered in code).")
    trace.append("History sent: highest " + (", ".join(f"{r['title']} {r['avg_rating']}/5" for r in h["highest"]) or "none")
                 + "; lowest " + (", ".join(f"{r['title']} {r['avg_rating']}/5" for r in h["lowest"]) or "none") + ".")

    picks: list[dict] = []
    user_input = p["user_input"]
    for attempt in range(1 + MAX_RETRIES):
        r = llm.call(ctx, AGENT_NAME, p["instructions"], user_input, max_output_tokens=MAX_OUTPUT_TOKENS,
                     cache_key=f"bridge-{AGENT_NAME}-couple-{couple_id}", json_schema=("ritual_picks", SCHEMA),
                     reasoning_effort=REASONING_EFFORT)
        try:
            answer = json.loads(r.text)
        except json.JSONDecodeError:
            answer = None
        good, problems = validate(answer, p["allowed"], p["history_text"], fingerprints, names)
        good = [g for g in good if g["ritual_id"] not in {x["ritual_id"] for x in picks}]
        picks += good[:N_PICKS - len(picks)]
        trace.append(f"{'Agent' if attempt == 0 else 'Retry'}: {r.usage.input_tokens:,} tokens in "
                     f"({r.usage.cached_tokens:,} cached), {r.usage.output_tokens:,} out. {len(good)} pick(s) passed."
                     + (" Answered from Portkey's response cache ($0)." if r.gateway_cache == "HIT" else ""))
        trace += [f"  Dropped: {x}" for x in problems]
        if len(picks) >= N_PICKS:
            break
        if attempt < MAX_RETRIES:
            user_input = (p["user_input"] + "\n<validation_feedback>\n" + "\n".join(f"- {escape(x)}" for x in problems)
                          + f"\nAlready accepted: {', '.join(x['ritual_id'] for x in picks) or 'none'}. Return "
                          f"{N_PICKS} picks that follow every rule.\n</validation_feedback>")
            trace.append("Retrying once with that feedback (max 1 retry).")

    for g in picks:
        g["source"] = "agent"
    if len(picks) < N_PICKS:
        extra = rule_picks(p["allowed"], h, {x["ritual_id"] for x in picks}, N_PICKS - len(picks))
        picks += [x | {"source": "rules"} for x in extra]
        trace.append(f"Filled {len(extra)} pick(s) from the rules (catalog + your ratings), no AI.")

    cat = rituals.catalog()
    result = [cat[x["ritual_id"]] | x for x in picks]
    trace.append(f"Result: {len(result)} rituals, {ctx.summary()['calls']} model call(s), ${ctx.summary()['cost_usd']:.5f}.")
    return {"time": time, "energy": energy, "picks": result, "trace": trace, "usage": ctx.summary(),
            "model": ctx.results[-1].model if ctx.results else None}

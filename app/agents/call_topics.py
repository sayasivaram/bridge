"""Call-Topic Agent: turns the couple's shared moments from the last 7 days into conversation
starters for their next call.

One run:
  1. Select shared moments from the last 7 days (moments.for_prompt with BOTH: private moments
     are never selected, not even the requester's own).
  2. Build the prompt. Stable part first (system prompt + couple profile, identical for both
     partners and every run, so it caches); variable part last (who's asking, the moments).
  3. Call gpt-6-luna with a strict JSON schema.
  4. Validate in code: every cited moment id must be one of the shared moments we sent, and no
     text may repeat a private moment or private pulse note. Bad items are dropped.
  5. If anything was dropped (or the JSON didn't parse): ONE retry with feedback, then merge.
     Never more than two model calls.
"""

from __future__ import annotations

import json
import re
from datetime import date, timedelta
from typing import Any

from app import moments, pulses, timeutil
from app.db import session
from app.llm import LLM, RunContext
from app.prompting import UNTRUSTED_DATA_RULES, escape, moments_block, wrap

AGENT_NAME = "call_topics"
PROMPT_VERSION = "v3"  # v1 baseline; v2 hook-don't-retell + whose labels; v3 low reasoning + no-copy example
# Model default is medium. low (v3) vs medium (v2), 5 runs each: mean cost $0.00042 vs $0.00107,
# 8s vs 16s, reasoning 320 vs 1,576 tokens, no empty answers; quality held (docs/evals).
REASONING_EFFORT = "low"
WINDOW_DAYS = 7
N_STARTERS = 5
MAX_OUTPUT_TOKENS = 2_500  # room for reasoning tokens plus ~600 tokens of JSON
MAX_RETRIES = 1

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["starters", "deeper_question"],
    "properties": {
        "starters": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["topic", "opener", "moment_ids"],
                "properties": {
                    "topic": {"type": "string"},
                    "opener": {"type": "string"},
                    "moment_ids": {"type": "array", "items": {"type": "integer"}},
                },
            },
        },
        "deeper_question": {
            "type": "object",
            "additionalProperties": False,
            "required": ["question", "why", "moment_ids"],
            "properties": {
                "question": {"type": "string"},
                "why": {"type": "string"},
                "moment_ids": {"type": "array", "items": {"type": "integer"}},
            },
        },
    },
}

SYSTEM_PROMPT = f"""\
You are the Call-Topic Agent in Bridge, an app that helps long-distance couples stay close.
Before a video call, you turn the small moments a couple dropped into their shared Moment Jar
into conversation starters, so the call skips the "how was your day?" loop and goes straight to
the things that actually happened.

## Your task
Write exactly {N_STARTERS} conversation starters and 1 deeper question for the partner named in
<request>, to use on their next call with the other partner.

## Rules
1. Use only details that appear in the <moments> block or the <couple_profile>. Never invent
   events, people, places, plans, feelings, or outcomes. If a moment doesn't say how something
   went, ask; don't assume. A short, true starter beats a vivid, made-up one.
2. Every starter must cite the id(s) of the moment(s) it is based on in moment_ids. Cite only ids
   that appear in the <moments> block. The deeper question cites at least one id too.
3. Be warm and specific. Name the actual thing (the song title, the race time, the cat in the
   lecture), never generic prompts like "How was your week?" or "What made you smile today?".
4. Hook, don't retell. Both partners can already read every moment in the jar, so a starter that
   repeats a moment adds nothing. Refer to the moment in a few words, then add something new: a
   curious question, a playful guess, a tease, or a callback to an inside joke. Never copy more
   than 5 words in a row from a moment. Don't begin two openers with the same three words.
5. Get the point of view right. Each moment says whose="yours" (written by the partner you are
   preparing) or whose="partner's" (written by the other partner). Every opener is what the
   partner you are preparing says out loud to the other one: one or two sentences, natural and
   affectionate.
   - whose="partner's": ask about it with curiosity. You weren't there, so don't describe it
     back to them.
   - whose="yours": write a teaser that makes the other partner want to hear the story, not the
     story itself. Never ask the other partner about something only the speaker experienced.
6. Balance. At least 2 of the {N_STARTERS} starters must get the other partner talking about their
   own life. If they shared few moments, build those on their shared stressor or an inside joke,
   citing the closest moment (one of theirs when there is one). Mix the moods, and don't build
   all five on one moment.
7. The deeper question goes a little beyond the week (hopes, what they miss, what they'd like to
   do together next) while staying grounded in a cited moment. Keep it gentle and open-ended.
   Don't assume shared history, habits, or plans the data doesn't mention (no "again" about
   something they never did together).
8. You are not a therapist or relationship judge. No diagnosing, no advice about whether the
   relationship should continue, no pressure. If a moment sounds stressful, make space for it
   kindly ("want to talk about it?") rather than analysing it.
9. You only ever see moments both partners chose to share. Never guess at, hint at, or ask about
   anything private, and never claim to know something that isn't in the data.
10. The stressors in the profile were shared by each partner on purpose. You may acknowledge them
   gently. Don't dramatize them.

{UNTRUSTED_DATA_RULES}
## Output
Return JSON that matches the schema:
- starters: {N_STARTERS} items, each with topic (a 2-5 word label), opener (what to say), and
  moment_ids (integers).
- deeper_question: question, why (one short sentence: why this question fits this week), and
  moment_ids.

## Example (a different couple, preparing Ana; their ids are made up and must never be cited)
The example shows the idea, not wording to reuse. Never copy its phrases or sentence patterns;
write every opener fresh, in words that fit this couple.
Moments: [e1] Ana, whose="yours" (funny): "My landlord's parrot learned to say my name and now
yells it at 6am." [e2] Sam, whose="partner's" (proud, song "Dreams - Fleetwood Mac"): "Played my
first open mic. Hands were shaking." [e3] Ana, whose="yours" (stressful): "Thesis draft due Friday
and my advisor hasn't replied in a week."
Good, about Sam's moment: topic "Sam's first open mic", opener "Okay, I need the full open-mic
story. Did your hands stop shaking once you started Dreams?", moment_ids [e2].
Good, about Ana's own moment: topic "The 6am parrot", opener "Guess who's been shouting my name
at 6am. Hint: feathers.", moment_ids [e1]. A teaser: it hints at the story without telling it.
Bad: "My landlord's parrot learned to say my name and now yells it at 6am." Reads the moment aloud.
Bad: "How did the open mic go? I heard everyone loved it!" Nobody said anyone loved it.
Bad: "How's your week been?" Generic, cites nothing.
Good deeper question: "If you could play one song just for me next time we're in the same room,
what would it be?" with why "Sam's open mic and his song choice", moment_ids [e2].
Bad deeper question: "Want to go to an open mic together again?" They never went together.
"""


def _day(d: date, with_weekday: bool = False) -> str:
    """'Oct 21' or 'Wednesday, October 21, 2026' (no %-d: it isn't supported on Windows)."""
    return f"{d:%A, %B} {d.day}, {d.year}" if with_weekday else f"{d:%b} {d.day}"


def couple_profile_block(conn, couple_id: int) -> str:
    """Stable per couple: names, places, time zones, inside jokes, shared stressors (all escaped)."""
    couple = conn.execute("SELECT * FROM couples WHERE id = ?", (couple_id,)).fetchone()
    partners = conn.execute("SELECT * FROM users WHERE couple_id = ? ORDER BY role", (couple_id,)).fetchall()
    lines = ["<couple_profile>", wrap("about", couple["profile"])]
    for p in partners:
        lines.append(wrap("partner", p["shared_stressors"] or "nothing shared", name=p["name"], role=p["role"],
                          city=p["city"], timezone=p["timezone"], label="stressed about (shared on purpose)"))
    for joke in filter(None, (j.strip() for j in couple["inside_jokes"].splitlines())):
        lines.append(wrap("inside_joke", joke))
    lines.append("</couple_profile>")
    return "\n".join(lines)


def build_prompt(conn, requester_id: int, today: date) -> dict[str, Any]:
    requester = conn.execute("SELECT * FROM users WHERE id = ?", (requester_id,)).fetchone()
    partner = conn.execute("SELECT * FROM users WHERE couple_id = ? AND id != ?",
                           (requester["couple_id"], requester_id)).fetchone()
    since = today - timedelta(days=WINDOW_DAYS - 1)  # local days in the requester's time zone
    tz = requester["timezone"]
    selected = moments.for_prompt(conn, requester["couple_id"], moments.BOTH, since, today, tz_name=tz)

    instructions = f"{SYSTEM_PROMPT}\n## This couple\n{couple_profile_block(conn, requester['couple_id'])}\n"
    request = wrap("request", f"Prepare {requester['name']} for their next call with {partner['name']}. "
                              f"Today is {_day(today, True)}. The moments below are every moment the couple "
                              f"shared from {_day(since)} to {_day(today)}.")
    return {
        "instructions": instructions,
        "user_input": f"{request}\n{moments_block(selected, reader_id=requester_id, tz_name=tz)}",
        "moments": selected,
        "requester": dict(requester),
        "partner": dict(partner),
        "since": since,
        "until": today,
    }


# --- Validation ------------------------------------------------------------------------------

def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", text.lower())


def _shingles(text: str, n: int = 5) -> set[str]:
    w = _words(text)
    if len(w) < 3:
        return set()
    n = min(n, len(w))
    return {" ".join(w[i:i + n]) for i in range(len(w) - n + 1)}


def private_fingerprints(conn, couple_id: int, allowed_texts: list[str]) -> set[str]:
    """5-word phrases from private moments and private pulse notes, minus phrases that also
    appear in the shared data we sent (so a coincidental overlap isn't flagged)."""
    private = moments.private_texts(conn, couple_id) + pulses.private_notes(conn, couple_id)
    allowed = set().union(*(_shingles(t) for t in allowed_texts)) if allowed_texts else set()
    return set().union(*(_shingles(t) for t in private)) - allowed if private else set()


def _check_ids(ids: Any, allowed: set[int], label: str, problems: list[str]) -> list[int] | None:
    if not isinstance(ids, list) or not ids or not all(isinstance(i, int) for i in ids):
        problems.append(f"{label} doesn't cite any moment id.")
        return None
    bad = [i for i in ids if i not in allowed]
    if bad:
        problems.append(f"{label} cites moment id {', '.join(map(str, bad))}, which is not one of the shared "
                        f"moments in <moments>.")
        return None
    return list(dict.fromkeys(ids))


def _leaks(texts: list[str], fingerprints: set[str]) -> bool:
    return bool(fingerprints and set().union(*(_shingles(t) for t in texts)) & fingerprints)


def validate(answer: Any, allowed: set[int], fingerprints: set[str]) -> tuple[list[dict], dict | None, list[str]]:
    """Keep only starters / deeper question that cite real shared moments and leak nothing."""
    problems: list[str] = []
    if not isinstance(answer, dict):
        return [], None, ["The answer was not a JSON object."]

    starters, seen = [], set()
    for n, s in enumerate(answer.get("starters") or [], 1):
        label = f"Starter {n}"
        if not isinstance(s, dict) or not all(isinstance(s.get(k), str) and s[k].strip() for k in ("topic", "opener")):
            problems.append(f"{label} is missing its topic or opener.")
            continue
        topic, opener = s["topic"].strip()[:80], s["opener"].strip()[:500]
        ids = _check_ids(s.get("moment_ids"), allowed, label, problems)
        if ids is None:
            continue
        if _leaks([topic, opener], fingerprints):
            problems.append(f"{label} repeats private content.")
            continue
        if topic.lower() in seen:
            problems.append(f"{label} repeats the topic '{topic}'.")
            continue
        seen.add(topic.lower())
        starters.append({"topic": topic, "opener": opener, "moment_ids": ids})

    deeper = None
    d = answer.get("deeper_question")
    if isinstance(d, dict) and isinstance(d.get("question"), str) and d["question"].strip():
        ids = _check_ids(d.get("moment_ids"), allowed, "The deeper question", problems)
        question, why = d["question"].strip()[:500], str(d.get("why") or "").strip()[:300]
        if ids is not None:
            if _leaks([question, why], fingerprints):
                problems.append("The deeper question repeats private content.")
            else:
                deeper = {"question": question, "why": why, "moment_ids": ids}
    else:
        problems.append("The deeper question is missing.")

    if len(starters) < N_STARTERS and not any("Starter" in p for p in problems):
        problems.append(f"Only {len(starters)} valid starters; {N_STARTERS} are needed.")
    return starters, deeper, problems


def _parse(text: str) -> Any:
    try:
        return json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None


# --- The run ---------------------------------------------------------------------------------

def _public_moment(m: dict) -> dict:
    return {k: m.get(k) for k in ("id", "author_name", "mood", "text", "song", "created_at")} | {
        "has_image": bool(m.get("image_file"))}


def run(llm: LLM, requester_id: int, today: date | None = None, ctx: RunContext | None = None) -> dict[str, Any]:
    """ctx: pass the orchestrator's context to share its request id and step limit."""
    ctx = ctx or RunContext(user_id=requester_id)
    trace: list[str] = []

    with session(llm.db_path) as conn:
        today = today or timeutil.local_today(moments.viewer_timezone(conn, requester_id))
        p = build_prompt(conn, requester_id, today)
        allowed = {m["id"] for m in p["moments"]}
        fingerprints = private_fingerprints(conn, p["requester"]["couple_id"], [m["text"] for m in p["moments"]])

    result: dict[str, Any] = {
        "requester": p["requester"]["name"], "partner": p["partner"]["name"],
        "window": {"since": p["since"].isoformat(), "until": p["until"].isoformat()},
        "moment_count": len(p["moments"]), "starters": [], "deeper_question": None, "trace": trace,
    }
    authors = sorted({m["author_name"] for m in p["moments"]})
    trace.append(f"Selected {len(p['moments'])} shared moments from {_day(p['since'])}–{_day(p['until'])}"
                 f"{' by ' + ' and '.join(authors) if authors else ''}. Private moments are never selected.")
    if not p["moments"]:
        trace.append("No model call: there's nothing to build starters from yet.")
        result["usage"] = ctx.summary()
        return result

    cache_key = f"bridge-{AGENT_NAME}-couple-{p['requester']['couple_id']}"
    user_input = p["user_input"]
    starters: list[dict] = []
    deeper = None
    for attempt in range(1 + MAX_RETRIES):
        r = llm.call(ctx, AGENT_NAME, p["instructions"], user_input, max_output_tokens=MAX_OUTPUT_TOKENS,
                     cache_key=cache_key, json_schema=("call_topics", SCHEMA), reasoning_effort=REASONING_EFFORT)
        answer = _parse(r.text)
        got, got_deeper, problems = validate(answer, allowed, fingerprints) if answer is not None else (
            [], None, ["The answer wasn't valid JSON."])
        label = "Call 1" if attempt == 0 else "Retry"
        trace.append(f"{label}: model returned {len((answer or {}).get('starters') or [])} starters "
                     f"({r.usage.input_tokens:,} tokens in, {r.usage.cached_tokens:,} cached, "
                     f"{r.usage.output_tokens:,} out). {len(got)} passed validation."
                     + (" Answered from Portkey's response cache, so it cost $0." if r.gateway_cache == "HIT" else ""))
        for problem in problems:
            trace.append(f"  Dropped: {problem}")

        topics = {s["topic"].lower() for s in starters}
        for s in got:
            if len(starters) < N_STARTERS and s["topic"].lower() not in topics:
                starters.append(s)
                topics.add(s["topic"].lower())
        deeper = deeper or got_deeper
        if len(starters) >= N_STARTERS and deeper:
            break
        if attempt < MAX_RETRIES:
            feedback = "\n".join(f"- {escape(x)}" for x in problems) or "- Not enough valid items."
            user_input = (f"{p['user_input']}\n<validation_feedback>\nYour previous answer broke these rules:\n"
                          f"{feedback}\nWrite a complete new answer that follows every rule. Cite only ids "
                          f"from <moments>.\n</validation_feedback>")
            trace.append("Retrying once with that feedback (max 1 retry).")

    by_id = {m["id"]: _public_moment(m) for m in p["moments"]}
    result["starters"] = [s | {"moments": [by_id[i] for i in s["moment_ids"]]} for s in starters]
    if deeper:
        result["deeper_question"] = deeper | {"moments": [by_id[i] for i in deeper["moment_ids"]]}
    result["usage"] = ctx.summary()
    result["model"] = ctx.results[-1].model if ctx.results else None
    trace.append(f"Result: {len(starters)} starters{' + a deeper question' if deeper else ''}, "
                 f"{ctx.summary()['calls']} model call(s), ${ctx.summary()['cost_usd']:.5f}.")
    return result

"""Router: maps a chat message to one agent.

Keyword rules first (free, instant, predictable). The LLM classifier runs only when the rules
are ambiguous: no match, or a tie between agents. Its answer must be one of the known intents
(strict JSON enum); anything else becomes "clarify", and the user picks.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from app.llm import LLM, RunContext
from app.prompting import UNTRUSTED_DATA_RULES, wrap

INTENTS = ("call_topics", "pulse_coach", "ritual_builder")
AGENT_LABEL = {"call_topics": "Call-Topic Agent", "pulse_coach": "Pulse Coach (Drift Detector)",
               "ritual_builder": "Ritual Builder"}

# (pattern, weight). Strong, specific phrases weigh 2; looser hints weigh 1.
RULES: dict[str, list[tuple[str, int]]] = {
    "call_topics": [
        (r"\bprep(?:are|aring)?\b", 2), (r"\b(?:our|the|next|a|tonight'?s|upcoming) (?:video )?call\b", 2),
        (r"\bconversation (?:starters?|topics?|ideas?)\b", 2), (r"\bwhat (?:should|can|do) i (?:ask|say|bring up)\b", 2),
        (r"\btalk(?:ing)? about\b", 1), (r"\btopics?\b", 1), (r"\bbring up\b", 1), (r"\bfacetime\b", 1),
    ],
    "pulse_coach": [
        (r"\bdistant\b", 2), (r"\bdisconnect", 2), (r"\bdrift(?:ing)?\b", 2), (r"\bgrowing apart\b", 2),
        (r"\b(?:feel|feeling|felt) (?:far|apart|off|lonely|less close)\b", 2), (r"\bout of sync\b", 2),
        (r"\bhow are we doing\b", 2), (r"\bnudge\b", 2), (r"\bpulse\b", 1), (r"\blonely\b", 1),
        (r"\bcheck[- ]?ins?\b", 1), (r"\bconnected\b", 1), (r"\bfar away\b", 1),
    ],
    "ritual_builder": [
        (r"\bwhat (?:should|can|could) we do\b", 2), (r"\bdo together\b", 2), (r"\britual", 2),
        (r"\bdate (?:night|idea)", 2), (r"\bsomething (?:fun|to do)\b", 2), (r"\btonight\b", 1), (r"\bbored\b", 1),
        (r"\bactivit", 1), (r"\bgames?\b", 1), (r"\bwatch\b", 1), (r"\bcook", 1), (r"\bweekend\b", 1),
    ],
}
_COMPILED = {i: [(re.compile(p, re.IGNORECASE), w) for p, w in rs] for i, rs in RULES.items()}


@dataclass
class Route:
    intent: str | None  # None = ask the user to pick
    method: str  # "rules" | "llm" | "clarify"
    scores: dict[str, int] = field(default_factory=dict)
    detail: str = ""


def rule_scores(message: str) -> dict[str, int]:
    text = " ".join(message.replace("’", "'").split())
    return {i: sum(w for p, w in pats if p.search(text)) for i, pats in _COMPILED.items()}


CLASSIFIER_PROMPT = f"""\
You route messages in Bridge, an app for long-distance couples, to one of three helpers:
- call_topics: preparing for an upcoming call: what to talk about, conversation starters.
- pulse_coach: how connected they feel lately: distance, drifting, feeling off, how they're doing as a couple.
- ritual_builder: something to do together: tonight, an activity, a date idea, a game, a show.
- none: anything else.
Reply with JSON: intent (one of the four) and a short reason. Pick none if it fits none of them.

{UNTRUSTED_DATA_RULES}"""

CLASSIFIER_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["intent", "reason"],
    "properties": {"intent": {"type": "string", "enum": [*INTENTS, "none"]}, "reason": {"type": "string"}},
}


def route(llm: LLM, ctx: RunContext, message: str) -> Route:
    scores = rule_scores(message)
    ranked = sorted(scores.items(), key=lambda kv: -kv[1])
    (top, top_score), (_, second) = ranked[0], ranked[1]
    if top_score > 0 and top_score > second:
        return Route(top, "rules", scores, f"keyword rules: {top} scored {top_score}, next best {second} ($0, no model)")

    why = "no keyword matched" if top_score == 0 else f"tie between {', '.join(i for i, s in ranked if s == top_score)}"
    try:
        r = llm.call(ctx, "router", CLASSIFIER_PROMPT, wrap("message", message), max_output_tokens=200,
                     json_schema=("route", CLASSIFIER_SCHEMA), reasoning_effort="low")
        answer = json.loads(r.text)
    except Exception as err:  # budget, API trouble, bad JSON: let the user pick
        return Route(None, "clarify", scores, f"{why}; classifier unavailable ({type(err).__name__}), asking the user")
    intent = answer.get("intent") if isinstance(answer, dict) else None
    if intent not in INTENTS:
        return Route(None, "clarify", scores, f"{why}; classifier said {intent!r}, asking the user")
    return Route(intent, "llm", scores, f"{why}, so the LLM classifier decided: {intent} "
                                        f"({r.usage.input_tokens} in, {r.usage.output_tokens} out)")


# --- Ritual Builder parameters from the message ---------------------------------------------

_TIME = [
    ("15", r"\b(?:5|10|15|20) ?(?:min|mins|minutes)\b|\bquick\b|\bshort\b|\bfew minutes\b|\bnot much time\b"),
    ("120", r"\b(?:2|two|3|three|couple of|a few) ?(?:hours|hrs)\b|\ball (?:evening|night)\b|\bwhole (?:evening|night)\b|\blots of time\b"),
    ("60", r"\b(?:an?|one|1) ?(?:hour|hr)\b|\bhour\b"),
]
_ENERGY = [
    ("low", r"\btired\b|\bexhausted\b|\bdrained\b|\bwiped\b|\bsleepy\b|\blow[- ]energy\b|\blong day\b|\blazy\b|\bcozy\b"),
    ("high", r"\benergetic\b|\bup for anything\b|\bwired\b|\bhigh[- ]energy\b|\badventurous\b|\bpumped\b"),
    ("medium", r"\bmedium\b|\bso-so\b|\bokay energy\b"),
]


def ritual_params(message: str) -> tuple[str, str, list[str]]:
    """(time, energy, assumptions) read from the message, with defaults it says it assumed."""
    time = next((v for v, p in _TIME if re.search(p, message, re.IGNORECASE)), None)
    energy = next((v for v, p in _ENERGY if re.search(p, message, re.IGNORECASE)), None)
    assumed = (["about an hour"] if time is None else []) + (["medium energy"] if energy is None else [])
    return time or "60", energy or "medium", assumed

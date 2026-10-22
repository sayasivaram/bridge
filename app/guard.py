"""Guard: checks that run before and after every agent in the chat orchestration.

Before (on the chat message):
  * length limits (empty, or longer than MAX_MESSAGE characters),
  * prompt-injection screening (the message only ever reaches the router's classifier, but a
    message that looks like an attack is refused outright, not classified),
  * the crisis pre-check (app/safety.py): support resources instead of any agent,
  * the coach boundary for relationship decisions ("should we break up?"): a gentle referral to a
    counselor instead of any agent,
After (on the agent's result):
  * schema validation: the result must match that agent's output contract (pydantic), or the
    user gets an error instead of half-formed output,
  * coach-boundary phrases in every user-facing text field (break up, diagnosing, blame...):
    the offending item is removed; a nudge is replaced with the quiet card.
The agents have their own checks too; this is the second, uniform layer.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app import rituals, safety
from app.agents.drift import QUIET, boundary_violations

MAX_MESSAGE = 300

INJECTION_PATTERNS = [
    r"\bignore (?:all |any |the |your )?(?:previous|prior|above|earlier|system) (?:instructions|prompts?|rules)\b",
    r"\b(?:system|developer) prompt\b", r"\byou are now\b", r"\bdeveloper mode\b", r"\bjailbreak",
    r"\b(?:reveal|show|print|tell me|give me|read me) .{0,40}\b(?:private|secret|hidden)\b",
    r"\bpretend (?:to be|you are)\b", r"\bact as (?:an?|the)\b", r"</?\s*(?:system|moment|moments|note|request)\b",
    r"\bdisregard (?:the |your )?(?:rules|instructions)\b",
]
_INJECTION = [re.compile(p, re.IGNORECASE) for p in INJECTION_PATTERNS]

# Coach boundary: a relationship decision is not an app's call. Answered with a gentle referral,
# before any agent or model runs, so no model can be talked into "you should break up".
DECISION_PATTERNS = [
    r"\bshould (?:we|i) (?:break up|split up|divorce|leave (?:him|her|them)|end (?:it|things|this|the relationship))\b",
    r"\b(?:tell|convince) (?:me|us|him|her|them) (?:that )?(?:we|i|they) should (?:break up|split up|leave|end it)\b",
    r"\bsay (?:that )?(?:we|you) should (?:break up|split up)\b",
    r"\bis (?:it|this|our relationship|this relationship) (?:over|worth saving|doomed)\b",
    r"\bshould (?:we|i) (?:stay together|give up on (?:us|this|him|her))\b",
]
_DECISION = [re.compile(p, re.IGNORECASE) for p in DECISION_PATTERNS]
DECISION_REPLY = ("That's a big question, and it isn't one an app should answer for you. A couples counselor "
                  "can help the two of you think it through together, at your own pace. If it would help, I can "
                  "suggest something to talk about on your next call.")


@dataclass
class InputVerdict:
    ok: bool
    kind: str = "ok"  # ok | empty | too_long | injection | crisis | decision
    reply: str = ""
    support: dict | None = None
    detail: str = ""


def check_input(message: str, tz_name: str | None) -> InputVerdict:
    text = (message or "").strip()
    if not text:
        return InputVerdict(False, "empty", "Type a question first.", detail="empty message")
    if len(text) > MAX_MESSAGE:
        return InputVerdict(False, "too_long", f"That's a bit long for me. Try one short sentence (under {MAX_MESSAGE} "
                            "characters).", detail=f"{len(text)} characters > limit {MAX_MESSAGE}")
    flag = safety.precheck(text)
    if flag:
        return InputVerdict(False, "crisis", "", safety.support_message(flag, tz_name),
                            detail=f"crisis pre-check matched ({flag.category}); no agent, no model")
    for p in _DECISION:
        m = p.search(text)
        if m:
            return InputVerdict(False, "decision", DECISION_REPLY,
                                detail=f"coach boundary: relationship-decision request ({m.group(0)!r}); "
                                       "gentle referral, no agent, no model")
    for p in _INJECTION:
        m = p.search(text)
        if m:
            return InputVerdict(False, "injection", "I can't help with that. I can prep you for a call, check how "
                                "you two are doing, or suggest a ritual for tonight.",
                                detail=f"injection pattern matched: {m.group(0)!r}; refused before any model call")
    return InputVerdict(True, detail=f"{len(text)} characters, no injection or crisis patterns")


# --- Output contracts (schema validation) ---------------------------------------------------

class _Out(BaseModel):
    model_config = ConfigDict(extra="allow")


class Starter(_Out):
    topic: str = Field(min_length=1, max_length=80)
    opener: str = Field(min_length=1, max_length=500)
    moment_ids: list[int] = Field(min_length=1)
    moments: list[dict] = Field(min_length=1)


class Deeper(_Out):
    question: str = Field(min_length=1, max_length=500)
    why: str = Field(max_length=300)
    moment_ids: list[int] = Field(min_length=1)


class CallTopicsOut(_Out):
    starters: list[Starter] = Field(max_length=5)
    deeper_question: Deeper | None
    usage: dict
    trace: list[str]


class NudgeOut(_Out):
    kind: Literal["nudge", "steady", "support"]
    suggestion: str | None = None
    explanation: str | None = None
    support: dict | None = None
    usage: dict

    @field_validator("suggestion", "explanation")
    @classmethod
    def _short(cls, v: str | None) -> str | None:
        if v is not None and not 1 <= len(v) <= 400:
            raise ValueError("must be one or two sentences")
        return v


class Pick(_Out):
    id: str
    name: str
    why: str = Field(min_length=1, max_length=400)
    first_step: str = Field(min_length=1, max_length=300)
    variation: str = Field(max_length=200)
    source: Literal["agent", "rules"]

    @field_validator("id")
    @classmethod
    def _in_catalog(cls, v: str) -> str:
        if v not in rituals.catalog():
            raise ValueError("not a catalog ritual")
        return v


class RitualsOut(_Out):
    picks: list[Pick] = Field(min_length=1, max_length=3)
    usage: dict
    trace: list[str]


CONTRACTS = {"call_topics": CallTopicsOut, "pulse_coach": NudgeOut, "ritual_builder": RitualsOut}


def check_output(intent: str, result: Any, names: list[str]) -> tuple[dict | None, list[str]]:
    """(sanitized result, notes). None means the result failed its contract and must not be shown."""
    try:
        CONTRACTS[intent].model_validate(result)
    except ValidationError as err:
        first = err.errors()[0]
        return None, [f"schema check failed: {'.'.join(map(str, first['loc']))}: {first['msg']}"]
    notes: list[str] = []
    out = dict(result)

    def hits(*texts: str | None) -> list[str]:
        return boundary_violations(" ".join(t for t in texts if t), names)

    if intent == "call_topics":
        kept = []
        for s in out["starters"]:
            bad = hits(s["topic"], s["opener"])
            if bad:
                notes.append("removed a starter: coach-boundary phrase (wording withheld)")
                print(f"guard: removed starter {s['topic']!r}: {bad}", file=sys.stderr)
            else:
                kept.append(s)
        out["starters"] = kept
        d = out.get("deeper_question")
        if d and hits(d["question"], d.get("why")):
            notes.append("removed the deeper question: coach-boundary phrase")
            out["deeper_question"] = None
    elif intent == "pulse_coach" and out["kind"] == "nudge":
        bad = hits(out.get("suggestion"), out.get("explanation"))
        if bad:
            notes.append("replaced the nudge with the quiet card: coach-boundary phrase (wording withheld)")
            print(f"guard: replaced nudge: {bad}", file=sys.stderr)
            out = {**QUIET, "flags": [], "trace": out.get("trace", []), "usage": out["usage"]}
    elif intent == "ritual_builder":
        kept = []
        for p in out["picks"]:
            bad = hits(p["why"], p["first_step"], p.get("variation"))
            if bad:
                notes.append(f"removed pick '{p['id']}': coach-boundary phrase (wording withheld)")
                print(f"guard: removed pick {p['id']!r}: {bad}", file=sys.stderr)
            else:
                kept.append(p)
        out["picks"] = kept
    if not notes:
        notes.append("schema valid; no coach-boundary phrases")
    return out, notes

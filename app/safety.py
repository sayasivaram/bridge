"""Coach-boundary pre-check: notes that suggest abuse, fear of a partner, self-harm, or serious
distress skip normal coaching entirely.

Deterministic keyword patterns, not an LLM: instant, free, testable, and a sensitive note is
never sent to a model. If a note matches:
  * its AUTHOR gets a brief, caring message with real support lines for their country, and
  * the drift agent is not called that week, and the OTHER partner only sees "no nudge this
    week": nothing about why (the partner may be the person they are afraid of).
Trade-off: keywords miss things a careful reader would catch, and can misfire. The patterns
avoid common figures of speech ("it hit me", "this deadline is killing me", "hopeless
romantic"), and the message says so gently in case we misread.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_PARTNER = r"(?:he|she|they|my (?:partner|boyfriend|girlfriend|husband|wife|fiance|fiancee|bf|gf))"

PATTERNS: dict[str, list[str]] = {
    "self_harm": [
        r"\bkill(?:ing)? myself\b", r"\bsuicid", r"\bself[- ]?harm", r"\bhurt(?:ing)? myself\b",
        r"\bcut(?:ting)? myself\b", r"\bend (?:it all|my life)\b", r"\bwant(?:ed)? to die\b",
        r"\b(?:don'?t|do not) want to (?:be here|live|be alive|wake up)\b", r"\bno reason to live\b",
        r"\bbetter off without me\b", r"\bwish i (?:was|were) dead\b",
    ],
    "abuse": [
        rf"\b{_PARTNER} (?:hits?|hit|hitting|slapped|slaps|shoved|choked|chokes|kicked|hurts?|hurt) me\b",
        rf"\b(?:afraid|scared|frightened|terrified) of {_PARTNER.replace('he|she|they', 'him|her|them')}\b",
        rf"\b(?:afraid|scared|frightened|terrified) (?:of )?what {_PARTNER}(?:'ll| will| might| would)\b",
        rf"\b{_PARTNER} threaten(?:s|ed)? (?:me|to)\b",
        rf"\b{_PARTNER} (?:won'?t|doesn'?t|does not|will not) let me (?:see|talk|leave|go|have)\b",
        r"\babus(?:e|ive|ed|ing)\b", r"\b(?:not|don'?t feel) safe (?:at home|with (?:him|her|them))\b",
        r"\bfeel unsafe\b",
    ],
    "distress": [
        r"\bcan'?t (?:cope|go on|take (?:it|this) any ?more)\b", r"\bhopeless\b(?! romantic)",
        r"\bpanic attacks?\b", r"\b(?:everything|i) (?:is|am|'m) falling apart\b", r"\bbreaking down\b",
    ],
}
_COMPILED = {cat: [re.compile(p, re.IGNORECASE) for p in ps] for cat, ps in PATTERNS.items()}
_ORDER = ("self_harm", "abuse", "distress")  # most urgent first


@dataclass(frozen=True)
class SafetyFlag:
    category: str  # self_harm | abuse | distress
    matched: str  # the phrase that matched (for logs/tests; never shown to the partner)


def _normalize(text: str) -> str:
    return " ".join(text.replace("’", "'").replace("‘", "'").split())


def precheck(text: str | None) -> SafetyFlag | None:
    """The most urgent category this text matches, or None."""
    if not text:
        return None
    text = _normalize(text)
    for cat in _ORDER:
        for pattern in _COMPILED[cat]:
            m = pattern.search(text)
            if m:
                return SafetyFlag(cat, m.group(0))
    return None


RESOURCES = {
    "US": {
        "self_harm": [("988 Suicide & Crisis Lifeline", "call or text 988")],
        "abuse": [("National Domestic Violence Hotline", "1-800-799-7233, or text START to 88788")],
        "distress": [("988 Suicide & Crisis Lifeline", "call or text 988, any time, for any kind of distress")],
        "emergency": "If you're in immediate danger, call 911.",
    },
    "UK": {
        "self_harm": [("Samaritans", "call 116 123 (free, 24/7)")],
        "abuse": [("National Domestic Abuse Helpline", "0808 2000 247 (free, 24/7)")],
        "distress": [("Samaritans", "call 116 123 (free, 24/7), for anything you're going through")],
        "emergency": "If you're in immediate danger, call 999.",
    },
    "OTHER": {
        "self_harm": [("Find a local crisis line", "findahelpline.com")],
        "abuse": [("Find a local helpline", "findahelpline.com")],
        "distress": [("Find a local helpline", "findahelpline.com")],
        "emergency": "If you're in immediate danger, call your local emergency number.",
    },
}

_BODY = {
    "self_harm": "It sounds like you might be going through something really painful. You deserve support "
                 "right now, from a person, not an app. Please reach out to one of these, or to someone you trust.",
    "abuse": "What you wrote sounds frightening. You deserve to feel safe. Closer won't share this with your "
             "partner. Trained, confidential advocates can help you think through your options.",
    "distress": "It sounds like this week has been really heavy. You don't have to carry that alone. Talking "
                "to someone you trust, or a counselor, can help. These lines are there any time.",
}


def country_for(tz_name: str | None) -> str:
    if tz_name == "Europe/London":
        return "UK"
    if tz_name and tz_name.startswith("America/"):
        return "US"
    return "OTHER"


def support_message(flag: SafetyFlag, tz_name: str | None) -> dict:
    """The caring message shown ONLY to the note's author, instead of normal coaching."""
    res = RESOURCES[country_for(tz_name)]
    return {
        "category": flag.category,
        "title": "We're glad you told us",
        "body": _BODY[flag.category],
        "resources": [{"name": n, "contact": c} for n, c in res[flag.category]],
        "emergency": res["emergency"],
        "footnote": "Closer is not a counseling service. If we misread your note, you can ignore this.",
    }

"""Evaluate the Call-Topic Agent: N real runs against fresh seed data, with automated checks.

    python scripts/eval_call_topics.py --label baseline           # 5 runs (default)
    python scripts/eval_call_topics.py --label v2 --runs 5
    python scripts/eval_call_topics.py --compare baseline v2 v3  # side-by-side metrics (no API calls)

Each run uses a freshly seeded throwaway database (your data/bridge.db is untouched) and asks
Portkey to skip its response cache, so every run is a real model call. Runs alternate between
Priya and Leo. Writes docs/evals/call_topics_<label>.json and .md.

Automated checks (hints for a human reviewer, not verdicts):
  generic      opener uses a stock phrase ("how was your week", ...)
  same_source  two starters in one run cite the same moment
  unsupported  a number or capitalized name in the text that appears in none of the cited
               moments or the couple profile (possible invented detail)
  own_voice    a starter cites only the requester's own moments but asks the partner about
               them ("what did you think of my...") instead of inviting the requester to share
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import tempfile
from collections import Counter
from datetime import date
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app.agents import call_topics  # noqa: E402
from app.config import load_settings  # noqa: E402
from app.db import session  # noqa: E402
from app.llm import LLM, build_client  # noqa: E402
from app.seed import seed  # noqa: E402

# Example lines from every prompt version's worked example (v1-v3), to catch copied wording.
EXAMPLE_LINES = [
    "Okay, I need the full open-mic story. Did your hands stop shaking once you started Dreams?",
    "Remind me to tell you what my landlord's parrot has started doing at sunrise. It's a whole saga.",
    "Guess who's been shouting my name at 6am. Hint: feathers.",
    "If you could play one song just for me next time we're in the same room, what would it be?",
]
GENERIC = ["how was your week", "how was your day", "what made you smile", "tell me about your day",
           "how have you been", "what's new", "anything exciting", "how are things", "catch up"]
CAPITALIZED_OK = {"I", "I'm", "I'd", "I've", "I'll", "You", "Your", "What", "How", "Did", "Do", "Does", "Was",
                  "Were", "When", "Where", "Why", "Who", "Which", "Okay", "OK", "So", "And", "But", "Also",
                  "Tell", "Want", "Would", "Could", "Can", "Is", "Are", "Have", "Has", "Had", "If", "That",
                  "This", "The", "A", "An", "Our", "We", "We're", "Let's", "Any", "Anything", "Still", "Hey",
                  "Honestly", "Wait", "Ok", "Oh", "My", "It", "It's", "There", "Then", "Now", "Since", "After",
                  "Before", "Just", "Also", "Speaking", "Yes", "No", "Not", "Every", "One", "Two", "Some"}


def _longest_copied_run(text: str, source: str) -> int:
    """Length of the longest run of consecutive words in `text` that also appears in `source`."""
    a, b = re.findall(r"[a-z0-9']+", text.lower()), re.findall(r"[a-z0-9']+", source.lower())
    best, prev = 0, [0] * (len(b) + 1)
    for x in a:
        cur = [0] * (len(b) + 1)
        for j, y in enumerate(b, 1):
            if x == y:
                cur[j] = prev[j - 1] + 1
                best = max(best, cur[j])
        prev = cur
    return best


def flags_for(item: dict, requester: str, profile_text: str) -> list[str]:
    texts = [item.get("topic", ""), item.get("opener") or item.get("question", ""), item.get("why", "")]
    out = []
    lower = " ".join(texts).lower()
    hits = [g for g in GENERIC if g in lower]
    if hits:
        out.append(f"generic: {', '.join(hits)}")
    source = " ".join(m["text"] + " " + (m.get("song") or "") + " " + m["author_name"] for m in item["moments"])
    source = (source + " " + profile_text).lower()
    unsupported = set()
    for t in texts:
        for sentence in re.split(r"(?<=[.!?—])\s+|—", t):
            words = re.findall(r"\b[A-Z][\w']+|\b\d[\d:.,]*\w*", sentence)
            if words and sentence.strip().startswith(words[0]):
                words = words[1:]  # capitalized only because it starts the sentence
            for w in words:
                if w in CAPITALIZED_OK or w.lower().rstrip("'s") in source or w.lower() in source:
                    continue
                unsupported.add(w)
    if unsupported:
        out.append(f"unsupported: {', '.join(sorted(unsupported))}")
    for m in item["moments"]:
        copied = _longest_copied_run(item.get("opener") or item.get("question", ""), m["text"])
        if copied >= 6:
            out.append(f"retells: copies {copied} words in a row from moment {m['id']}")
            break
    authors = {m["author_name"] for m in item["moments"]}
    opener = item.get("opener", "")
    if authors == {requester} and re.search(r"\b(what did you think|did you like|how did you feel about) (of )?(my|that)\b",
                                            opener, re.I):
        out.append("own_voice: asks the partner about the requester's own moment")
    return out


def run_eval(label: str, runs: int) -> dict:
    settings = load_settings()
    # Portkey: skip its response cache so each run is a real model call (prompt caching still applies).
    client = build_client(settings).with_options(default_headers={"x-portkey-cache-force-refresh": "true"})
    results = []
    with tempfile.TemporaryDirectory() as tmp:
        for n in range(1, runs + 1):
            db_path = Path(tmp) / f"run{n}.db"
            seed(db_path)
            requester_id = 1 if n % 2 else 2
            llm = LLM(settings, db_path=db_path, client=client)
            out = call_topics.run(llm, requester_id)
            with session(db_path) as conn:
                calls = [dict(r) for r in conn.execute(
                    "SELECT input_tokens, cached_tokens, cache_write_tokens, output_tokens, reasoning_tokens, "
                    "cost_usd, latency_ms, gateway_cache FROM llm_calls ORDER BY id")]
            with session(db_path) as conn:
                profile_text = call_topics.couple_profile_block(conn, 1)
            results.append({"run": n, "requester": out["requester"], "result": out, "calls": calls,
                            "profile_text": profile_text})
            u = out["usage"]
            print(f"run {n} ({out['requester']}): {len(out['starters'])} starters, calls={u['calls']}, "
                  f"in={u['input_tokens']} cached={u['cached_tokens']} out={u['output_tokens']} "
                  f"(reasoning {u['reasoning_tokens']}) ${u['cost_usd']:.5f} "
                  f"gateway={[c['gateway_cache'] for c in calls]}")
    return {"label": label, "date": date.today().isoformat(), "prompt_version": call_topics.PROMPT_VERSION,
            "runs": results}


def annotate(ev: dict) -> dict:
    """Add automated flags to every starter and deeper question (idempotent)."""
    for r in ev["runs"]:
        out = r["result"]
        cited = Counter(i for s in out["starters"] for i in s["moment_ids"])
        items = out["starters"] + ([out["deeper_question"]] if out["deeper_question"] else [])
        for item in items:
            item["flags"] = flags_for(item, out["requester"], r.get("profile_text", ""))
        for s in out["starters"]:
            if any(cited[i] > 1 for i in s["moment_ids"]):
                s["flags"].append("same_source: another starter cites the same moment")
    return ev


def to_markdown(ev: dict) -> str:
    rows = []
    for r in ev["runs"]:
        u = r["result"]["usage"]
        lat = sum(c["latency_ms"] or 0 for c in r["calls"])
        rows.append(f"| {r['run']} | {r['requester']} | {u['calls']} | {u['input_tokens']:,} | {u['cached_tokens']:,} | "
                    f"{u['cache_write_tokens']:,} | {u['output_tokens']:,} | {u['reasoning_tokens']:,} | "
                    f"${u['cost_usd']:.5f} | {lat / 1000:.1f}s | {len(r['result']['starters'])} |")
    costs = [r["result"]["usage"]["cost_usd"] for r in ev["runs"]]
    lines = [f"# Call-Topic Agent eval: {ev['label']} ({ev['date']})", "",
             "| Run | For | Calls | Input | Cached | Cache write | Output | Reasoning | Cost | Latency | Starters |",
             "|---|---|---|---|---|---|---|---|---|---|---|", *rows, "",
             f"Mean cost per run: ${sum(costs) / len(costs):.5f} (min ${min(costs):.5f}, max ${max(costs):.5f})", ""]
    starters = [(r, s) for r in ev["runs"] for s in r["result"]["starters"]]
    openings = Counter(" ".join(s["opener"].split()[:3]).lower() for _, s in starters)
    kinds = Counter(f.split(":")[0] for _, s in starters for f in s["flags"])
    deeper_kinds = Counter(f.split(":")[0] for r in ev["runs"] if r["result"]["deeper_question"]
                           for f in r["result"]["deeper_question"]["flags"])
    about_partner = sum(any(m["author_name"] != r["requester"] for m in s["moments"]) for r, s in starters)
    retries = sum(r["result"]["usage"]["calls"] - 1 for r in ev["runs"])
    lines += [
        "## Summary",
        "",
        f"- Starters: {len(starters)}; flagged: " + (", ".join(f"{k} {v}" for k, v in kinds.most_common()) or "none"),
        f"- Deeper questions flagged: " + (", ".join(f"{k} {v}" for k, v in deeper_kinds.most_common()) or "none"),
        f"- Starters about the partner's moments: {about_partner}/{len(starters)} "
        f"(the rest are about the requester's own moments)",
        f"- Retries needed: {retries}",
        "- Most common opener beginnings: " + ", ".join(f'"{k}" x{v}' for k, v in openings.most_common(5)),
        "",
    ]
    for r in ev["runs"]:
        res = r["result"]
        lines += [f"## Run {r['run']}: for {r['requester']}", ""]
        for i, s in enumerate(res["starters"], 1):
            src = "; ".join(f"[{m['id']}] {m['author_name']}" for m in s["moments"])
            flag = f"  \n  ⚠️ {' | '.join(s['flags'])}" if s["flags"] else ""
            lines.append(f"{i}. **{s['topic']}**: {s['opener']}  \n  _from {src}_{flag}")
        d = res["deeper_question"]
        if d:
            flag = f"  \n  ⚠️ {' | '.join(d['flags'])}" if d["flags"] else ""
            lines.append(f"\n**Deeper:** {d['question']}  \n_why: {d['why']}_{flag}")
        drops = [t for t in res["trace"] if "Dropped" in t or "Retry" in t]
        if drops:
            lines.append("\nValidation: " + " / ".join(t.strip() for t in drops))
        lines.append("")
    return "\n".join(lines)


def metrics(ev: dict) -> dict:
    """Numbers for a before/after table."""
    ev = annotate(ev)
    runs = ev["runs"]
    usage = [r["result"]["usage"] for r in runs]
    starters = [(r, s) for r in runs for s in r["result"]["starters"]]
    mean = lambda xs: sum(xs) / len(xs) if xs else 0  # noqa: E731
    copied = [max((_longest_copied_run(s["opener"], m["text"]) for m in s["moments"]), default=0) for _, s in starters]
    repeated_openings = 0
    for r in runs:
        firsts = Counter(" ".join(s["opener"].lower().split()[:3]) for s in r["result"]["starters"])
        repeated_openings += sum(n for n in firsts.values() if n > 1)
    flags = Counter(f.split(":")[0] for _, s in starters for f in s["flags"])
    deeper = [r["result"]["deeper_question"] for r in runs if r["result"]["deeper_question"]]
    example_copies = sum(any(_longest_copied_run(s["opener"], ex) >= 4 for ex in EXAMPLE_LINES) for _, s in starters)
    empty = sum((c["output_tokens"] or 0) - (c["reasoning_tokens"] or 0) <= 0 for r in runs for c in r["calls"])
    top_open, top_n = Counter(" ".join(s["opener"].lower().split()[:3]) for _, s in starters).most_common(1)[0]
    priya_runs = [r for r in runs if r["requester"] == "Priya"]
    return {
        "Runs": len(runs),
        "Mean cost per run": f"${mean([u['cost_usd'] for u in usage]):.5f}",
        "Cost range": f"${min(u['cost_usd'] for u in usage):.5f} to ${max(u['cost_usd'] for u in usage):.5f}",
        "Mean input tokens": f"{mean([u['input_tokens'] for u in usage]):,.0f}",
        "Mean cached input tokens": f"{mean([u['cached_tokens'] for u in usage]):,.0f}",
        "Mean output tokens (reasoning)": f"{mean([u['output_tokens'] for u in usage]):,.0f} "
                                          f"({mean([u['reasoning_tokens'] for u in usage]):,.0f})",
        "Mean latency": f"{mean([sum(c['latency_ms'] or 0 for c in r['calls']) for r in runs]) / 1000:.1f}s",
        "Retries": sum(u["calls"] - 1 for u in usage),
        "Empty answers (whole cap spent on reasoning)": empty,
        "Starters returned": f"{len(starters)}/{5 * len(runs)}",
        "Starters that retell (6+ words copied)": f"{flags['retells']}/{len(starters)}",
        "Mean longest copied run (words)": f"{mean(copied):.1f}",
        "Openers sharing first 3 words within a run": repeated_openings,
        "Most common opening across runs": f'"{top_open}" x{top_n}',
        "Openers copying 4+ words from a prompt example": f"{example_copies}/{len(starters)}",
        "Starters about the partner's moments": f"{sum(any(m['author_name'] != r['requester'] for m in s['moments']) for r, s in starters)}/{len(starters)}",
        "Starters about Leo in Priya's runs": f"{sum(any(m['author_name'] == 'Leo' for m in s['moments']) for r in priya_runs for s in r['result']['starters'])}/{sum(len(r['result']['starters']) for r in priya_runs)}",
        "Flags: generic / unsupported / same_source": f"{flags['generic']} / {flags['unsupported']} / {flags['same_source']}",
        "Deeper questions citing the headphones moment": f"{sum(any('headphones' in m['text'] for m in d['moments']) for d in deeper)}/{len(deeper)}",
    }


def compare(*labels: str) -> str:
    out_dir = REPO / "docs" / "evals"
    ms = [metrics(json.loads((out_dir / f"call_topics_{x}.json").read_text())) for x in labels]
    lines = [f"# Call-Topic Agent: {' vs '.join(labels)}", "",
             "| Metric | " + " | ".join(labels) + " |", "|---" * (len(labels) + 1) + "|"]
    lines += [f"| {k} | " + " | ".join(str(m[k]) for m in ms) + " |" for k in ms[0]]
    md = "\n".join(lines) + "\n"
    (out_dir / f"call_topics_compare_{'_'.join(labels)}.md").write_text(md)
    return md


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="baseline")
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--report-only", action="store_true", help="rebuild the .md from the saved .json (no API calls)")
    ap.add_argument("--compare", nargs="+", metavar="LABEL", help="compare saved evals (no API calls)")
    args = ap.parse_args()
    if args.compare:
        print(compare(*args.compare))
        return
    out_dir = REPO / "docs" / "evals"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / f"call_topics_{args.label}.json"
    if args.report_only:
        ev = json.loads(json_path.read_text())
    else:
        ev = run_eval(args.label, args.runs)
        json_path.write_text(json.dumps(ev, indent=2, ensure_ascii=False))  # raw results first, before anything can fail
    ev = annotate(ev)
    json_path.write_text(json.dumps(ev, indent=2, ensure_ascii=False))
    (out_dir / f"call_topics_{args.label}.md").write_text(to_markdown(ev))
    print(f"wrote docs/evals/call_topics_{args.label}.md")


if __name__ == "__main__":
    main()

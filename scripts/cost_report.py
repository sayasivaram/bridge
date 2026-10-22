"""Cost report: average tokens and cost per run for each agent, then monthly cost projections.

    python scripts/cost_report.py                       # from data/closer.db, or the measured sample
    python scripts/cost_report.py --per-week call_topics=3 ritual_builder=1
    python scripts/cost_report.py --users 1 100 10000 50000
    python scripts/cost_report.py --measure 3           # run every agent live 3x (~$0.006), save sample
    python scripts/cost_report.py --json                # machine-readable

A "run" is one user request; if an agent retried, both calls count toward that run.

Data source, in order: --csv PATH, --db PATH, data/closer.db if it has any calls, else
docs/usage/measured_calls.csv (real calls measured with --measure and committed, so the report
works on a fresh clone).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app import config, usage  # noqa: E402
from app.db import DB_PATH, session  # noqa: E402
from app.llm import load_prices  # noqa: E402

SAMPLE_CSV = REPO / "docs" / "usage" / "measured_calls.csv"

# ===== ASSUMPTIONS: runs per user per week. Edit these, or override with --per-week. =====
ASSUMPTIONS = {
    "call_topics": 2.0,  # "Prep for our call" before their two calls a week
    "ritual_builder": 2.0,  # two evenings a week they ask for ritual ideas
    "drift": 0.5,  # one Pulse nudge per couple per week, shared by both partners (cached)
    "router": 1.0,  # one chat message a week that the keyword rules can't route
}
WEEKS_PER_MONTH = 52 / 12
DEFAULT_USERS = (1, 100, 10_000)
CSV_FIELDS = ["created_at", "agent_name", "user_name", "request_id", "via_chat", "status", "model", "gateway_cache",
              "input_tokens", "cached_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens", "cost_usd",
              "latency_ms"]


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="") as f:
        rows = list(csv.DictReader(f))
    for r in rows:
        for k in ("input_tokens", "cached_tokens", "cache_write_tokens", "output_tokens", "reasoning_tokens",
                  "latency_ms", "via_chat"):
            r[k] = int(r[k] or 0)
        r["cost_usd"] = float(r["cost_usd"] or 0)
    return rows


def read_db(path: Path) -> list[dict]:
    with session(path) as conn:
        return usage.load_rows(conn)


def load(args) -> tuple[list[dict], str]:
    if args.csv:
        path = Path(args.csv).resolve()
        return read_csv(path), str(path.relative_to(REPO)) if path.is_relative_to(REPO) else str(path)
    if args.db:
        return read_db(Path(args.db)), str(args.db)
    if DB_PATH.exists():
        rows = read_db(DB_PATH)
        if rows:
            return rows, f"{DB_PATH.relative_to(REPO)} (your app's own calls)"
    return read_csv(SAMPLE_CSV), f"{SAMPLE_CSV.relative_to(REPO)} (measured sample)"


def project(summary: dict, per_week: dict[str, float], users: list[int]) -> dict:
    by_agent = {a["agent"]: a for a in summary["by_agent"]}
    lines, per_user_month = [], 0.0
    for agent, runs_week in per_week.items():
        a = by_agent.get(agent)
        if not a:
            lines.append({"agent": agent, "runs_per_week": runs_week, "missing": True})
            continue
        cost_run = a["avg_per_run"]["cost_usd"]
        month = runs_week * WEEKS_PER_MONTH * cost_run
        per_user_month += month
        tokens_run = a["avg_per_run"]["input_tokens"] + a["avg_per_run"]["output_tokens"]
        lines.append({"agent": agent, "runs_per_week": runs_week, "cost_per_run": cost_run,
                      "tokens_per_run": tokens_run, "cost_per_user_month": round(month, 6),
                      "tokens_per_user_month": round(runs_week * WEEKS_PER_MONTH * tokens_run)})
    ceiling = usage.monthly_ceiling_per_user(load_prices())
    return {"lines": lines, "cost_per_user_month": round(per_user_month, 6),
            "users": {n: round(per_user_month * n, 6) for n in users},
            "ceiling_per_user_month": ceiling, "ceiling_users": {n: round(ceiling * n, 2) for n in users}}


def money(x: float) -> str:
    return f"${x:,.2f}" if x >= 1 else f"${x:.4f}" if x >= 0.01 else f"${x:.5f}"


def print_report(summary: dict, proj: dict, source: str, per_week: dict[str, float]) -> None:
    t = summary["total"]
    print(f"Closer cost report\nSource: {source}\n{t['calls']} model calls in {t['runs']} runs, "
          f"{money(t['cost_usd'])} total; prompt-cache share {t['cached_share']:.0%}; "
          f"Portkey response-cache hits {t['gateway_hits']}\n")
    print("Average per run, by agent")
    print(f"  {'agent':<22}{'runs':>5}{'calls/run':>10}{'input':>8}{'cached':>8}{'cache wr':>9}{'output':>8}"
          f"{'reason':>8}{'cost/run':>11}")
    for a in summary["by_agent"]:
        p = a["avg_per_run"]
        print(f"  {a['label']:<22}{a['runs']:>5}{p['calls']:>10.2f}{p['input_tokens']:>8,.0f}{p['cached_tokens']:>8,.0f}"
              f"{p['cache_write_tokens']:>9,.0f}{p['output_tokens']:>8,.0f}{p['reasoning_tokens']:>8,.0f}"
              f"{money(p['cost_usd']):>11}")
    print("\nAssumptions (runs per user per week; edit ASSUMPTIONS or use --per-week):")
    for line in proj["lines"]:
        if line.get("missing"):
            print(f"  {line['agent']:<16} {line['runs_per_week']:>4}/wk  (no measured runs; not counted)")
        else:
            print(f"  {line['agent']:<16} {line['runs_per_week']:>4}/wk  x {money(line['cost_per_run'])}/run  ->  "
                  f"{money(line['cost_per_user_month'])} per user per month")
    print(f"\nProjected monthly cost (x {WEEKS_PER_MONTH:.2f} weeks/month):")
    print(f"  {'users':>8}  {'expected':>12}  {'budget ceiling':>15}")
    for n, cost in proj["users"].items():
        print(f"  {n:>8,}  {money(cost):>12}  {money(proj['ceiling_users'][n]):>15}")
    print(f"\n  expected: {money(proj['cost_per_user_month'])} per user per month from the measured averages.")
    print(f"  ceiling: {money(proj['ceiling_per_user_month'])} per user per month is the most a user can cost if they hit "
          f"the {config.DEFAULT_DAILY_TOKEN_BUDGET:,}-token daily budget every day at the highest rate.")


def measure(rounds: int) -> Path:
    """Run every agent live `rounds` times on fresh seed data (Portkey response cache skipped so
    every call is real) and save the calls to docs/usage/measured_calls.csv."""
    from app import orchestrator
    from app.agents import call_topics, drift, ritual_builder
    from app.config import load_settings
    from app.llm import LLM
    from app.seed import seed
    from scripts.safety_cases import LiveClient

    times, energies = ["15", "60", "120", "60"], ["low", "medium", "high", "low"]
    chats = ["feeling a bit off lately", "any ideas for us this evening?", "what could I ask about this week?",
             "hmm, not sure what we need"]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "measure.db"
        seed(path)
        llm = LLM(load_settings(), db_path=path, client=LiveClient())
        for i in range(rounds):
            who = 1 if i % 2 == 0 else 2
            print(f"round {i + 1}/{rounds} ({'Priya' if who == 1 else 'Leo'})", flush=True)
            call_topics.run(llm, who)
            with session(path) as conn:
                conn.execute("DELETE FROM nudges")  # measure a fresh nudge each round
            drift.run(llm, who)
            ritual_builder.run(llm, who, times[i % 4], energies[i % 4])
            orchestrator.handle(llm, who, chats[i % 4])
        rows = read_db(path)
    SAMPLE_CSV.parent.mkdir(parents=True, exist_ok=True)
    with SAMPLE_CSV.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"saved {len(rows)} calls to {SAMPLE_CSV.relative_to(REPO)}")
    return SAMPLE_CSV


def parse_per_week(items: list[str] | None) -> dict[str, float]:
    per_week = dict(ASSUMPTIONS)
    for item in items or []:
        agent, _, n = item.partition("=")
        if agent not in ASSUMPTIONS or not n:
            sys.exit(f"--per-week expects agent=N with agent in {', '.join(ASSUMPTIONS)} (got {item!r})")
        per_week[agent] = float(n)
    return per_week


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db"), ap.add_argument("--csv")
    ap.add_argument("--per-week", nargs="+", metavar="AGENT=N", help="runs per user per week, e.g. call_topics=3")
    ap.add_argument("--users", nargs="+", type=int, default=list(DEFAULT_USERS))
    ap.add_argument("--measure", type=int, metavar="ROUNDS", help="measure live first (each round ~$0.002)")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    if args.measure:
        args.csv = str(measure(args.measure))
    rows, source = load(args)
    if not rows:
        sys.exit("No model calls to report yet. Use the app (or run with --measure 3) first.")
    summary = usage.summarize(rows)
    per_week = parse_per_week(args.per_week)
    proj = project(summary, per_week, args.users)
    if args.json:
        print(json.dumps({"source": source, "summary": summary, "assumptions": per_week, "projection": proj}, indent=2))
    else:
        print_report(summary, proj, source, per_week)


if __name__ == "__main__":
    main()

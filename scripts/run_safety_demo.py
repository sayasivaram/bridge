"""Safety demo: runs seven attacks and malfunctions against the real app code and prints what
each defense did. Made for screenshots.

    python scripts/run_safety_demo.py            # all 7, scripted model (free, same output every time)
    python scripts/run_safety_demo.py --live     # cases 1, 2, 7 against the real gpt-6-luna (~$0.002)
    python scripts/run_safety_demo.py --case 5   # just one

Every case uses its own freshly seeded throwaway database; your data/bridge.db is untouched.
Exit code is 1 if any defense fails.
"""

from __future__ import annotations

import argparse
import os
import sys
import textwrap
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from safety_cases import CASES, LIVE_CASES  # noqa: E402

WIDTH = 100
COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if COLOR else text


def wrap(text: str, indent: int) -> str:
    return textwrap.fill(text, WIDTH, initial_indent="", subsequent_indent=" " * indent)


def show(r) -> None:
    print(c(f"\n━━ {r.number}. {r.title} " + "━" * max(3, WIDTH - len(r.title) - 7), "1"))
    print(wrap(f"{c('Attack:', '1;31')}  {r.attack}", 9))
    print(f"{c('Model:', '2')}   {r.model}")
    print(c("What happened:", "1"))
    for label, detail in r.steps:
        line = f"  • {label}" + (f": {detail}" if detail else "")
        print(wrap(line, 6))
    print(c("Checks:", "1"))
    for name, ok in r.checks.items():
        print(f"  {c('✔', '32') if ok else c('✘', '31')} {name}")
    print(wrap(f"{c('Defenses:', '2')} " + " · ".join(r.defenses), 10))
    verdict = c("✅ DEFENDED", "1;32") if r.passed else c("❌ FAILED", "1;31")
    print(f"{verdict}   ({calls(r)})")


def calls(r) -> str:
    if not r.model_calls:
        return "0 model calls, $0"
    kind = "real" if r.live else "scripted"
    return f"{r.model_calls} {kind} model call{'s' * (r.model_calls != 1)}, ${r.cost_usd:.5f}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--live", action="store_true", help="use the real gpt-6-luna for cases 1, 2 and 7")
    ap.add_argument("--case", type=int, choices=range(1, 8), help="run one case")
    args = ap.parse_args()

    mode = "live gpt-6-luna for cases 1, 2, 7" if args.live else "scripted model (plays a compromised model)"
    print(c(f"Bridge safety demo · {mode}", "1"))
    results = []
    for fn in CASES:
        n = int(fn.__name__.split("_")[1])
        if args.case and n != args.case:
            continue
        r = fn(live=args.live and n in LIVE_CASES)
        show(r)
        results.append(r)

    print(c("\n━━ Summary " + "━" * (WIDTH - 11), "1"))
    for r in results:
        mark = c("✅", "32") if r.passed else c("❌", "31")
        print(f"  {mark} {r.number}. {r.title:<46} {sum(r.checks.values())}/{len(r.checks)} checks   {calls(r)}")
    passed = sum(r.passed for r in results)
    print(c(f"\n{passed} of {len(results)} defended.", "1;32" if passed == len(results) else "1;31"))
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(main())

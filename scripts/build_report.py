"""Build report.html: one self-contained file (images embedded), 10-15 printed pages.

    python scripts/build_report.py            # writes report.html at the repo root

Screenshots come from docs/screenshots and docs/*.png and are embedded as compressed JPEGs (macOS
`sips` if available, else the original PNG). Numbers in the text come from docs/evals,
docs/usage/measured_calls.csv (via the same code as scripts/cost_report.py), and data/prices.json.
Placeholders the author must fill in are marked with class="todo" (yellow boxes).
"""

from __future__ import annotations

import base64
import html
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app import usage  # noqa: E402
from app.llm import load_prices  # noqa: E402
from scripts import cost_report  # noqa: E402

SHOTS = REPO / "docs" / "screenshots"
OUT = REPO / "report.html"
_tmp = Path(tempfile.mkdtemp())


def img(path: Path, width: int = 1500, quality: int = 72) -> str:
    """Data URI for an image, re-encoded as a smaller JPEG when possible."""
    if shutil.which("sips"):
        out = _tmp / (path.stem + ".jpg")
        subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", str(quality), "-Z", str(width),
                        str(path), "--out", str(out)], check=True, capture_output=True)
        return "data:image/jpeg;base64," + base64.b64encode(out.read_bytes()).decode()
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def fig(name: str, caption: str, width: int = 100, src_dir: Path = SHOTS, max_px: int = 1500, cls: str = "") -> str:
    return (f'<figure class="{cls}" style="width:{width}%"><img src="{img(src_dir / name, max_px)}" alt="{html.escape(caption)}">'
            f"<figcaption>{caption}</figcaption></figure>")


def pair(a: str, b: str) -> str:
    return f'<div class="pair">{a}{b}</div>'


def money(x: float) -> str:
    return f"${x:,.2f}" if x >= 1 else f"${x:.4f}" if x >= 0.01 else f"${x:.5f}"


# --- numbers from the repo ---------------------------------------------------------------------

rows = cost_report.read_csv(cost_report.SAMPLE_CSV)
summary = usage.summarize(rows)
proj = cost_report.project(summary, dict(cost_report.ASSUMPTIONS), [1, 100, 10_000])
prices = load_prices()
by_agent = {a["agent"]: a for a in summary["by_agent"]}
latency = {}
for a in by_agent:
    ms = sorted(int(r["latency_ms"]) for r in rows if r["agent_name"] == a)
    latency[a] = ms[len(ms) // 2] / 1000

AGENT_ROWS = [("call_topics", "Call-Topic Agent", "Prep for our call"), ("ritual_builder", "Ritual Builder", "Rituals"),
              ("drift", "Drift Agent", "Pulse nudge"), ("router", "Router classifier", "Ask Bridge (ambiguous only)")]


def per_run_table() -> str:
    body = ""
    for key, label, feature in AGENT_ROWS:
        a = by_agent[key]
        p = a["avg_per_run"]
        body += (f"<tr><td>{label}</td><td>{feature}</td><td class=n>{a['runs']}</td><td class=n>{p['input_tokens']:,.0f}</td>"
                 f"<td class=n>{p['cached_tokens']:,.0f}</td><td class=n>{p['output_tokens']:,.0f}</td>"
                 f"<td class=n>{p['reasoning_tokens']:,.0f}</td><td class=n>{latency[key]:.1f}s</td>"
                 f"<td class=n><b>{money(p['cost_usd'])}</b></td></tr>")
    return ("<table><tr><th>Agent</th><th>Feature</th><th class=n>Runs</th><th class=n>Input</th><th class=n>Cached</th>"
            "<th class=n>Output</th><th class=n>of which reasoning</th><th class=n>Median time</th><th class=n>Cost / run</th></tr>"
            + body + "</table>")


def cost_chart() -> str:
    """Horizontal bars: cost per run by agent (one hue, direct labels, no legend needed)."""
    data = [(label, by_agent[key]["avg_per_run"]["cost_usd"]) for key, label, _ in AGENT_ROWS]
    top = max(v for _, v in data)
    w, bar_h, gap, left, right = 640, 22, 12, 150, 90
    h = len(data) * (bar_h + gap) + 6
    parts = []
    for i, (label, v) in enumerate(data):
        y = i * (bar_h + gap) + 4
        bw = (w - left - right) * v / top
        parts.append(f'<text x="{left - 10}" y="{y + 16}" text-anchor="end" class="cl">{label}</text>'
                     f'<rect x="{left}" y="{y}" width="{bw:.1f}" height="{bar_h}" rx="4" fill="#d9665b"/>'
                     f'<text x="{left + bw + 8:.1f}" y="{y + 16}" class="cv">{money(v)}</text>')
    return (f'<figure><svg viewBox="0 0 {w} {h}" role="img" aria-label="Cost per run by agent">{"".join(parts)}</svg>'
            "<figcaption>Average cost per run, measured on 18 real gpt-6-luna calls "
            "(<code>docs/usage/measured_calls.csv</code>). Output tokens dominate; input is mostly read from the prompt "
            "cache at a tenth of the price.</figcaption></figure>")


def projection_table() -> str:
    lines = {l["agent"]: l for l in proj["lines"]}
    assume = "".join(
        f"<tr><td>{label}</td><td class=n>{lines[key]['runs_per_week']:g}</td><td class=n>{money(lines[key]['cost_per_run'])}</td>"
        f"<td class=n>{money(lines[key]['cost_per_user_month'])}</td></tr>" for key, label, _ in AGENT_ROWS)
    scale = "".join(f"<tr><td class=n>{n:,}</td><td class=n><b>{money(c)}</b></td><td class=n>{money(proj['ceiling_users'][n])}</td></tr>"
                    for n, c in proj["users"].items())
    return (f'<div class="pair tables"><div><table><tr><th>Assumption, per user</th><th class=n>Runs / week</th>'
            f"<th class=n>Cost / run</th><th class=n>Per month</th></tr>{assume}"
            f"<tr class=total><td>Total</td><td></td><td></td><td class=n><b>{money(proj['cost_per_user_month'])}</b></td></tr></table></div>"
            f"<div><table><tr><th class=n>Users</th><th class=n>Expected / month</th><th class=n>Hard ceiling / month</th></tr>"
            f"{scale}</table></div></div>")


v = {"call": by_agent["call_topics"]["avg_per_run"], "rit": by_agent["ritual_builder"]["avg_per_run"],
     "drift": by_agent["drift"]["avg_per_run"], "router": by_agent["router"]["avg_per_run"]}
cached_share = summary["total"]["cached_share"]
input_cost_cached = (v["call"]["cached_tokens"] * prices["cached_input_per_1m"] + v["call"]["cache_write_tokens"]
                     * prices["cache_write_per_1m"] + (v["call"]["input_tokens"] - v["call"]["cached_tokens"]
                                                       - v["call"]["cache_write_tokens"]) * prices["input_per_1m"]) / 1e6
input_cost_uncached = v["call"]["input_tokens"] * prices["cache_write_per_1m"] / 1e6  # HW6: uncached input billed as writes
per_couple_month = 2 * proj["cost_per_user_month"]

TODO = '<div class="todo"><b>To fill in before submitting:</b> {}</div>'

# --- the report -------------------------------------------------------------------------------

SECTIONS = f"""
<header class="title">
  <div class="kicker">AI Foundations · Final project · October 2026</div>
  <h1>Bridge</h1>
  <p class="lede">A multi-agent app that helps long-distance couples stay close between calls.</p>
  <table class="meta">
    <tr><td>Author</td><td>Saya Sivaram</td></tr>
    <tr><td>Code</td><td><span class="todo-inline">[GITHUB REPO URL]</span> (the same link submitted on Canvas)</td></tr>
    <tr><td>App</td><td>Runs locally from the repo in about two minutes (README: clone, venv, install, <code>.env</code> one folder up, seed, run)</td></tr>
  </table>
  <p class="oneline"><b>In one sentence:</b> Bridge turns the small moments two partners drop into a shared jar during the
    week into specific conversation starters for their next call, notices when the couple starts to drift, and suggests a
    ritual to do together tonight, while keeping each partner's private notes private.</p>
  {fig("00-home.png", "Bridge's Home page, viewing as Priya (New Haven; Leo lives in London, five hours ahead). Ask Bridge routes a question to the right agent; the bubbles below open each feature.", 86)}
</header>

<section>
<h2>2. Motivation</h2>
<p><b>Why I built this.</b> I moved away from my partner to attend Yale for two years, and so did several of my
classmates. Between recruiting, classes, clubs, and everything else, it is easy for a relationship to take the back
seat. My partner also travels abroad for months at a time (right now, Europe for the next six weeks), and we found
ourselves simply connecting less. This is my life partner, and I don't want to come out of these two years with a
weaker relationship. So I wanted a tool that helps us stay connected and still fits into both of our crazy
schedules.</p>
<p>Couples who live apart, such as grad students like us, early-career professionals, people waiting on a visa, or military
families, keep a relationship going mostly through scheduled calls. Those calls have a known failure mode: they open with
"how was your day?", slide into logistics (flights, schedules, who calls when), and end before anything that actually
happened that week comes up. The funny thing on the train, the song stuck in your head, the small win at work: by the
time of the call they're forgotten, or they feel too small to bring up.</p>
<p>The second problem is slower. Distance hides drift. Partners in the same city notice when the other is quieter than
usual; partners five time zones apart often don't, until it surfaces as a fight. In the demo couple's own words (seed
data, written to mirror this pattern): Priya's week-3 check-in note says <i>"Calls felt rushed this week. We mostly
talked logistics."</i> Leo's private note the same week says <i>"Felt far away this week."</i> Neither knows the
other felt it.</p>
<p><b>How people deal with it today:</b> a running notes app or a starred-messages folder of "things to tell them",
daily-question apps that ask both partners the same generic question, a shared calendar, or pasting texts into ChatGPT
and asking for ideas. Each covers a piece. None of them uses what <i>this</i> couple actually lived this week, and none
notices drift.</p>
<p><b>Why anyone should care:</b> calls are the relationship for these couples, and making them better is cheap. Bridge
costs about {money(per_couple_month)} per couple per month to run (section 5), so it could be offered free.</p>
</section>

<section>
<h2>3. What is new here</h2>
<p>I looked at the couples apps that come up most often in reviews and app stores, plus ChatGPT. Feature descriptions
below come from each app's own site or store listing and from review roundups, so they are self-reported.</p>
<table class="compare">
  <tr><th>Product</th><th>What it does</th><th>Uses this couple's own week?</th><th>Partner privacy</th><th>Notices drift</th></tr>
  <tr><td><b>Paired</b></td><td>A daily question and quizzes for both partners; "1000+ expert-led quizzes and games"; ~5 minutes a day</td><td>No: same questions for every couple</td><td>Answers revealed to each other</td><td>No</td></tr>
  <tr><td><b>Lasting</b></td><td>Self-guided "Series" of lessons (communication, conflict), journaling, therapist-led workshops; not a substitute for counseling</td><td>No</td><td>n/a</td><td>No</td></tr>
  <tr><td><b>Couply</b></td><td>Quizzes, date ideas, daily feeling check-ins, an AI coach, a long-distance mode</td><td>Partly: AI advice, not from shared moments</td><td>Unclear</td><td>No</td></tr>
  <tr><td><b>Love Nudge</b></td><td>Love-language goals and "nudges", a "Love Tank" gauge</td><td>No</td><td>n/a</td><td>A self-reported gauge</td></tr>
  <tr><td><b>Pairy, Amora, CoupleSpace</b></td><td>Private chat, shared calendar, photo albums or a shared journal, daily questions</td><td>Stores moments, but doesn't use them for prompts</td><td>A shared space</td><td>No</td></tr>
  <tr><td><b>ChatGPT</b></td><td>Can write conversation starters from whatever you paste in</td><td>Only what one partner pastes</td><td>None: it sees what you paste</td><td>No</td></tr>
  <tr class="us"><td><b>Bridge</b></td><td>Call starters from the week's shared moments, a weekly 30-second Pulse, rituals that learn from ratings</td><td><b>Yes</b>, and every starter cites the moment it came from</td><td><b>Per item</b>, enforced in the database queries</td><td><b>Yes</b>: rules first, then one gentle suggestion</td></tr>
</table>
<p class="small">Sources: <a href="https://www.paired.com/">paired.com</a>; Lasting on the <a href="https://apps.apple.com/app/id1225049619">App Store</a>
and <a href="https://www.choosingtherapy.com/lasting-app-review/">ChoosingTherapy's review</a>; Couply on <a href="https://appshunter.io/ios/app/1484241314">AppsHunter</a>;
Love Nudge in <a href="https://www.marriage.com/advice/communication/apps-long-distance-relationship/">marriage.com's roundup</a>;
<a href="https://apps.apple.com/app/id522155251">Pairy</a>; Amora on <a href="https://www.producthunt.com/products/amora-3/makers">Product Hunt</a>;
CoupleSpace on <a href="https://mwm.ai/apps/couplespace-relationship-app/6757912257">mwm.ai</a>.</p>
<p><b>What Bridge does that these don't.</b> Each piece exists somewhere: journals, daily questions, AI coaches. What is
new is putting them together around the call, and doing it safely:</p>
<ul>
  <li><b>Grounded in the couple's real week.</b> Starters come only from moments the couple logged, and code checks that
    every one cites a real, shared moment. A generic question app can't say "I need the full story about the cat in the
    backpack at your lecture."</li>
  <li><b>Privacy between partners is a feature, not a setting.</b> Each moment and note is shared or private. Private
    items never reach the other partner or any prompt whose output the other partner sees. Scores are revealed only
    after both have checked in, so neither anchors on the other.</li>
  <li><b>Drift detection that is cheap and honest.</b> Plain rules (score trends, a gap between partners, fewer moments,
    missed check-ins) run for free every week. The model is called only when they flag something, and then only to
    write one gentle, specific suggestion, never a diagnosis.</li>
</ul>
<p><b>Honest limits of the claim:</b> I didn't use every app in the table myself, and their features change. And it
isn't one of the course's overdone ideas: not a chatbot over documents, a planner, a summarizer, or a study buddy. The
LLM writes from this couple's own data, under rules checked in code.</p>
</section>

<section>
<h2>4. The agents</h2>
<p>Bridge has three agents and a router, all calling gpt-6-luna through one wrapper (<code>app/llm.py</code>). Every page
button calls its agent directly; the <b>Ask Bridge</b> chat box goes through an orchestrator that checks the input, routes
it, runs the agent, and checks the output.</p>
<table>
  <tr><th>Component</th><th>Runs when</th><th>Can use (its tools)</th><th>Returns</th><th>Checked in code</th></tr>
  <tr><td><b>Call-Topic Agent</b></td><td>"Prep for our call", or the chat</td><td>Shared moments from the last 7 days; couple profile (names, time zones, inside jokes, shared stressors)</td><td>5 starters + 1 deeper question, each citing moment ids</td><td>Ids must be shared moments in the window; no private text; 1 retry</td></tr>
  <tr><td><b>Drift Detector</b></td><td>Pulse page, or the chat</td><td>Layer 1 rules (no model): 4-week signals. Layer 2 agent: those signals, shared notes, ritual ratings</td><td>One suggestion + a short explanation</td><td>Coach-boundary phrases, numbers must come from the signals; 1 retry, then a rule-based nudge</td></tr>
  <tr><td><b>Ritual Builder</b></td><td>Rituals page, or the chat</td><td>Catalog of 25 rituals (filtered to tonight's time and energy in code); highest- and lowest-rated past rituals with notes</td><td>3 catalog rituals: why, first step, optional twist</td><td>Ids in the catalog and fitting tonight; no invented bookings or purchases; 1 retry, then rule picks</td></tr>
  <tr><td><b>Router</b></td><td>Chat messages only</td><td>Keyword rules first; an LLM classifier only on a tie or no match</td><td>One of the three agents, or "ask the user"</td><td>Strict enum; anything else asks the user</td></tr>
</table>
{fig("architecture-system.png", "Architecture. Agents read data only through a privacy layer whose rules live in SQL. Every model call goes through llm.py, which enforces the token budget, the step limit, and the model lock, and logs tokens and cost.", 40, REPO / "docs", 1400, "float-right")}
<p><b>How they're organized.</b> It's a hub, not a chain: agents don't call each other. The orchestrator owns one request
context, so the router and agent share a request id, a step limit (6 calls), and one token total. Each agent validates
its own output in code and gets at most one retry; the guard then checks the output again (schema and coach boundary)
before anything is shown. Rules do the work wherever rules are enough: routing clear messages, detecting drift,
filtering the ritual catalog, and screening for crisis language all cost nothing.</p>
<p><b>Model.</b> gpt-6-luna through Portkey is the only model allowed on the course key, so every agent uses it, with
strict JSON-schema output so answers can be validated. I set <b>reasoning effort to "low"</b> after measuring it (section 5):
it cut the Call-Topic Agent's cost by 61% and its latency in half, with no loss on the quality checks.</p>

<h3>One real run, step by step</h3>
<p>Priya types <i>"What should we do tonight?"</i> into the chat. The trace below is the app's own log of that request
(<code>agent_runs</code>), shown under the answer:</p>
<ol>
  <li><b>Guard in:</b> 26 characters, no injection pattern, no crisis language. It passes.</li>
  <li><b>Router:</b> keyword rules score Ritual Builder 3 ("what should we do", "tonight") and everything else 0. A clear
    winner, so no model call: $0.</li>
  <li><b>Ritual Builder:</b> code filters the catalog to the 20 rituals that fit about an hour at medium energy (the
    router read neither from the message, so it says what it assumed). It sends the couple's history: highest rated, a
    co-watch and the pasta cook-along (both 5.0/5); lowest, Codenames Duet (2.5/5, "too much strategy after a long
    work day").</li>
  <li><b>Model call:</b> 2,950 tokens in (2,463 read from the prompt cache), 244 out, {money(0.00021)}. It returns three picks, each with why it fits, a first step, and an
    optional twist.</li>
  <li><b>Validation:</b> all three ids are in the catalog and fit tonight, there are no bookings or purchases, and the ratings
    it cites (5/5) match the history. 3 of 3 pass.</li>
  <li><b>Guard out:</b> the output matches its schema and has no coach-boundary phrases.</li>
  <li><b>Logged</b> with the trace, tokens, and cost; the badge shows the total.</li>
</ol>
{fig("24-chat-end-to-end-rules-routed.png", "The run above as Priya saw it, with “How this was handled” open. Stage labels match the steps.", 72)}
{pair(fig("12-call-prep-starters.png", "Call-Topic Agent for Leo: five starters, each showing the moment it came from, and a deeper question. 2,365 of 2,368 input tokens came from the prompt cache.", 100, max_px=1200),
      fig("15-pulse-drift-nudge.png", "Drift Detector: the rules found three flags for $0; the agent turned them into one suggestion grounded in the couple's best-rated ritual.", 100, max_px=1200))}
</section>

<section>
<h2>5. Tokenomics</h2>
<p>Every model call is logged with its input, cached-input, cache-write, output, and reasoning tokens and its cost
(<code>llm_calls</code>), and the app shows them: a badge on every answer and a <b>Usage</b> page. The numbers below are
real runs, measured with <code>python scripts/cost_report.py --measure 4</code> on fresh seed data (18 calls, Portkey's
response cache skipped so every call reached the model). Prices: gpt-6-luna per 1M tokens, input ${prices['input_per_1m']},
cached ${prices['cached_input_per_1m']}, cache write ${prices['cache_write_per_1m']}, output ${prices['output_per_1m']}.</p>
{per_run_table()}
{cost_chart()}
<h3>Cost per user, and at scale</h3>
<p>Assumptions, per user per week: call prep before two calls, ritual ideas twice, one Pulse nudge per couple (so half
per user, because both partners share it), and one chat message the keyword rules can't route. They're editable at the
top of <code>scripts/cost_report.py</code>.</p>
{projection_table()}
<p><b>Expected cost: {money(proj['cost_per_user_month'])} per user per month</b>, about {money(per_couple_month)} per couple.
The <b>hard ceiling</b> is what a user would cost if they hit the {50_000:,}-token daily budget every day, with every token
at the most expensive rate: {money(proj['ceiling_per_user_month'])} per user per month. The budget makes that ceiling a guarantee,
not an estimate: a request that could go over is refused before the model is called (section 6).</p>

<h3>What I did to keep cost down</h3>
<ol>
  <li><b>Prompt layout for caching.</b> Every agent sends the stable part first (rules, the ritual catalog, the couple
    profile, byte-identical between runs and between partners) and the variable part last. {cached_share:.0%} of all input tokens
    were read from the cache. For a call prep, input costs about {money(input_cost_cached)} instead of {money(input_cost_uncached)}.
    (Homework 6 measured that uncached input on this deployment is billed as cache writes at $0.125.)</li>
  <li><b>Reasoning effort "low".</b> Default (medium) reasoning used 1,576 reasoning tokens per call-prep run in v2;
    low used 320. Cost per run went from $0.00107 to $0.00042, and latency from 16 s to 8 s.</li>
  <li><b>Rules before models.</b> Clear chat messages are routed by keywords ($0). The Drift Detector's rules run for
    free and the model is called only on drift, so a quiet week costs nothing. The ritual catalog is filtered to
    tonight's time and energy in code, so the model only chooses.</li>
  <li><b>Never pay twice.</b> A nudge is cached per couple and signals, so both partners share one call. Portkey also
    answers repeated identical requests from its own cache. <code>llm.py</code> reads Portkey's
    <code>x-portkey-cache-status</code> header, so those hits cost $0 and don't use up the budget, instead of being counted
    at full price.</li>
  <li><b>Budget and caps.</b> A per-user daily token budget, a step limit of 6 calls per request, a cap on output tokens
    per call, and at most one retry per agent.</li>
</ol>

<h3>Did cutting cost make it worse? The evidence</h3>
<p>I evaluated the Call-Topic Agent's prompt in three versions, 5 real runs each, with automated checks plus my own
reading of every output (<code>scripts/eval_call_topics.py</code>, results in <code>docs/evals/</code>):</p>
<table>
  <tr><th>Call-Topic Agent, 5 runs each</th><th class=n>v1 baseline</th><th class=n>v2 better prompt</th><th class=n>v3 = v2 + low reasoning</th></tr>
  <tr><td>Mean cost per run</td><td class=n>$0.00065</td><td class=n>$0.00107</td><td class=n><b>$0.00042</b></td></tr>
  <tr><td>Mean latency</td><td class=n>16.7 s</td><td class=n>16.1 s</td><td class=n><b>8.0 s</b></td></tr>
  <tr><td>Reasoning tokens per run</td><td class=n>734</td><td class=n>1,576</td><td class=n>320</td></tr>
  <tr><td>Starters that just retell their moment (6+ words copied)</td><td class=n>14 / 25</td><td class=n>0 / 25</td><td class=n>0 / 25</td></tr>
  <tr><td>Openers copying the prompt's example</td><td class=n>0 / 25</td><td class=n>5 / 25</td><td class=n>0 / 25</td></tr>
  <tr><td>Empty answers (whole output cap spent on reasoning)</td><td class=n>0</td><td class=n>1</td><td class=n>0</td></tr>
  <tr><td>Starters about Leo in Priya's runs</td><td class=n>3 / 15</td><td class=n>6 / 15</td><td class=n>5 / 15</td></tr>
</table>
<p>The better prompt (v2) fixed the quality problems but cost 65% more, because the model thought much longer, and once it
spent its whole output cap on reasoning and returned nothing (the retry recovered it). Lowering reasoning effort (v3) brought
cost below the original with the quality gains intact. The one place v3 looks slightly worse is balance: one of three
Priya runs had only one starter about Leo, against the rule's two. With 5 runs per version, that difference is within
run-to-run noise; the large effects (retelling 14 → 0, cost, latency) are not.</p>
{fig("29-usage-page.png", "The Usage page on a fresh install after a few real requests: totals, each partner's daily allowance against the budget, and cost by feature, agent, and partner.", 58)}
</section>

<section>
<h2>6. Safety</h2>
<p>The risks that matter for an app that holds two people's private feelings about each other:</p>
<table>
  <tr><th>Risk</th><th>What I built</th><th>Evidence</th></tr>
  <tr><td><b>Prompt injection</b> in a moment, a note, or the chat</td><td>User text is HTML-escaped and wrapped in data tags; every system prompt says tag contents are data, never instructions. Chat messages are screened before routing.</td><td>Demo cases 1–2; <code>tests/test_prompt_injection.py</code>, <code>test_call_topics.py</code></td></tr>
  <tr><td><b>A partner reading the other's private moments or notes</b></td><td>The privacy rule lives inside every SQL query in <code>moments.py</code> and <code>pulses.py</code>, and a test fails if any other code queries those tables. Prompts for one partner never select the other's private items.</td><td>Demo case 3; <code>test_moments_privacy.py</code>, <code>test_pulse.py</code></td></tr>
  <tr><td><b>Invented facts</b></td><td>Each agent's output is checked in code: cited moments must exist and be shared, ritual ids must be in the catalog, numbers and ratings must come from the data.</td><td>Demo case 4; agent tests</td></tr>
  <tr><td><b>The coach overstepping</b>: diagnosing, predicting, taking sides, "you should break up"</td><td>Boundary-phrase checks on every agent's output (retry, then a safe rule-based answer); "should we break up?" gets a gentle referral to a counselor before any model runs.</td><td>Demo case 7; <code>test_orchestrator.py</code>, <code>test_safety.py</code></td></tr>
  <tr><td><b>Abuse, fear, self-harm, serious distress</b></td><td>A keyword pre-check runs before any coaching, on private notes too. The author gets real helplines for their country (988 and the National DV Hotline in the US; Samaritans and the National Domestic Abuse Helpline in the UK). The other partner sees an ordinary quiet week, because they may be the person the author is afraid of. Flagged text never goes to a model.</td><td>Demo case 5; <code>test_pulse.py</code></td></tr>
  <tr><td><b>Overspending</b> and runaway loops</td><td>Per-user daily token budget checked before every call; step limit of 6 per request; output cap; one retry.</td><td>Demo case 6; <code>test_llm.py</code></td></tr>
</table>
<p><b>Defenses shown working.</b> <code>python scripts/run_safety_demo.py</code> runs seven attacks against the real app
code. By default the model is <i>scripted to act compromised</i>, so it tries to do whatever each attack asks; this shows
the code-side checks catching it even when the model misbehaves. All seven are defended. With <code>--live</code>, the
real gpt-6-luna ignored both injections and gave safe answers on its own (3 cases, {money(0.00057)}).</p>
{pair(fig("27-safety-demo-summary.png", "The safety demo's summary: seven attacks and malfunctions, all defended.", 100, max_px=1300),
      fig("07-what-the-model-receives.png", "What the model actually receives: the injection arrives escaped inside its tag, and Priya's private moment is never selected for output to Leo.", 100, max_px=1300))}
{pair(fig("18-crisis-support-for-author.png", "Leo's note trips the crisis pre-check: a private support card with UK helplines, no coaching.", 100, max_px=1200),
      fig("19-crisis-partner-sees-quiet-card.png", "The same week from Priya's side: the ordinary quiet card, with no hint of why.", 100, max_px=1200))}
<p><b>Do the tests really test something?</b> For each defense, I deleted it in a throwaway copy of the code and ran its
tests: removing the privacy condition from the SQL fails 5 privacy tests; removing the reveal rule fails 3; turning off the
crisis pre-check fails 2; turning off the coach-boundary check fails 4; turning off the catalog check fails 3. In total
there are {125} test functions ({166} cases), and none of them spends tokens.</p>
{fig("03-privacy-side-by-side.png", "The same week in both jars. Leo's private moment (“Still behind at work…”) appears only in his own.", 72)}
</section>

<section>
<h2>7. Impact</h2>
<p><b>What Bridge changes, with the numbers I can stand behind:</b></p>
<ul>
  <li><b>Prep goes from effort to seconds.</b> Instead of scrolling back through a week of texts before a call, a partner
    presses one button and gets five specific starters in a median of {latency['call_topics']:.1f} s, each linked to the moment it came from.</li>
  <li><b>Specific, not generic.</b> On the final prompt, 0 of 25 starters just retold a moment and none was generic
    ("how was your week?"); every one cited a real shared moment (automated checks, 5 real runs).</li>
  <li><b>Something that wasn't possible before: noticing drift privately.</b> The rules caught the demo couple's pattern
    (Leo's score 4 → 3 → 2, his shared moments 6 → 3 → 1, both saying contact was too little) and suggested one concrete
    fix, without revealing either partner's private notes to the other.</li>
  <li><b>Cheap enough to be free:</b> about {money(per_couple_month)} per couple per month, and at most
    {money(2 * proj['ceiling_per_user_month'])} even in the worst case. A nonprofit or university counseling center could
    offer it to every student in a long-distance relationship for a few dollars a month.</li>
</ul>
{TODO.format("a real user test. Even one couple for 15 minutes counts, and it is the strongest evidence. A simple protocol: "
             "(1) ask one partner to write down what they'd talk about on their next call, timing it; (2) have them drop 5–10 moments "
             "from their week into Bridge (or use the demo couple) and press Prep for our call; (3) ask them to rate the starters 1–5 for "
             "“would actually use this” and pick their favorite; (4) after the call, ask how many starters they used. Report the time "
             "before and after, the ratings, one quote, and a screenshot. If you can't run it in time, say so plainly here and present "
             "the bullets above as estimates.")}
</section>

<section>
<h2>8. Limits and next steps</h2>
<p><b>What doesn't work yet:</b></p>
<ul>
  <li><b>No real accounts.</b> "Viewing as" is a demo switch, so anyone running the app can view as either partner. The
    privacy rules hold for each viewer, but real logins are needed before real couples use it.</li>
  <li><b>The crisis check is keyword-based.</b> It is instant and testable, and it avoids common figures of speech ("this
    deadline is killing me"), but it will miss things a careful reader would catch and can misfire. Its card says so.</li>
  <li><b>The page buttons skip the chat's output guard.</b> Each agent checks its own output, but only chat requests get
    the second, uniform check.</li>
  <li><b>Small evaluations.</b> 5 runs per prompt version is enough to see large effects, not small ones. Starter quality
    was judged by automated checks and by me, not by couples.</li>
  <li><b>Portkey's response cache</b> returns the same answer to an identical request: free, but "Prep" twice with no new
    moments gives the same starters.</li>
  <li>Tested on Python 3.14 on macOS; Python 3.11 and Windows are supported by the code and dependencies but untested.</li>
</ul>
<p><b>With another month:</b></p>
<ol>
  <li><b>A real pilot</b> with 5–10 long-distance couples for three weeks, measuring how many starters they use per call
    and how their weekly Pulse scores move.</li>
  <li><b>Accounts and an invite flow:</b> one partner invites the other; both can export or delete their data.</li>
  <li><b>Time-zone-aware reminders</b> an hour before a call, with that call's starters ready, and a nudge to drop a moment
    when the jar has been quiet for days.</li>
  <li><b>A second safety layer:</b> an LLM classifier behind the keyword pre-check, tested on a labeled set of notes, and the
    output guard on every path, not just the chat.</li>
  <li><b>Learning per couple:</b> let ratings of starters ("we used this one") feed the Call-Topic Agent the way ritual ratings
    already feed the Ritual Builder.</li>
</ol>
<p class="small">How this report was made: every number comes from files in the repo and can be reproduced with
<code>scripts/cost_report.py</code>, <code>scripts/eval_call_topics.py --compare baseline v2 v3</code>, and
<code>scripts/run_safety_demo.py</code>. The report was drafted with an AI assistant and checked against the code.</p>
</section>
"""

CSS = """
:root { --ink:#2b2433; --muted:#6f667a; --accent:#d9665b; --accent-dark:#b8483e; --soft:#fbe6e2; --line:#eadfe4; --bg:#faf6f2; }
* { box-sizing: border-box; }
body { margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.55 -apple-system, "Segoe UI", Roboto, sans-serif; }
main { max-width: 860px; margin: 0 auto; padding: 36px 40px 60px; background: #fff; }
h1 { font: 700 46px/1.1 Georgia, serif; color: var(--accent); margin: 4px 0 6px; }
h2 { font: 700 23px/1.25 Georgia, serif; color: var(--ink); margin: 34px 0 10px; padding-top: 10px; border-top: 2px solid var(--soft); }
h3 { font-size: 16px; margin: 22px 0 6px; }
p, li { margin: 0 0 9px; }
ul, ol { padding-left: 22px; margin: 0 0 10px; }
.kicker { text-transform: uppercase; letter-spacing: .08em; font-size: 12px; color: var(--muted); font-weight: 600; }
.lede { font-size: 19px; margin: 0 0 14px; }
.meta { width: auto; margin: 0 0 12px; } .meta td { border: 0; padding: 2px 14px 2px 0; } .meta td:first-child { color: var(--muted); }
.oneline { background: var(--soft); border-radius: 10px; padding: 10px 14px; }
table { width: 100%; border-collapse: collapse; margin: 10px 0 14px; font-size: 12.5px; }
th { text-align: left; background: #f6eff2; color: var(--ink); font-weight: 600; padding: 6px 8px; border-bottom: 1px solid var(--line); }
td { padding: 6px 8px; border-bottom: 1px solid var(--line); vertical-align: top; }
td.n, th.n { text-align: right; font-variant-numeric: tabular-nums; white-space: nowrap; }
tr.us td { background: #fff4f1; } tr.total td { border-top: 2px solid var(--line); }
.compare td:first-child { white-space: nowrap; }
figure { margin: 14px auto; break-inside: avoid; page-break-inside: avoid; }
figure img { width: 100%; display: block; border-radius: 8px; border: 1px solid var(--line); }
figure svg { width: 100%; height: auto; display: block; }
figcaption { font-size: 12px; color: var(--muted); margin-top: 5px; }
.pair { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; align-items: start; break-inside: avoid; }
.pair figure { margin: 10px 0; }
figure.float-right { float: right; margin: 4px 0 10px 18px; }
h2 { clear: both; }
.tables > div { min-width: 0; }
.cl { font-size: 13px; fill: var(--ink); } .cv { font-size: 13px; fill: var(--muted); font-weight: 600; }
code { font: 12px Menlo, Consolas, monospace; background: #f6eff2; padding: 1px 4px; border-radius: 4px; }
a { color: var(--accent-dark); }
.small { font-size: 12px; color: var(--muted); }
.todo { background: #fff3bf; border: 1px dashed #d4a300; border-radius: 8px; padding: 10px 14px; margin: 10px 0 14px; font-size: 14px; }
.todo-inline { background: #fff3bf; padding: 0 4px; border-radius: 3px; }
@page { size: letter; margin: 0.6in 0.65in; }
@media print {
  body { background: #fff; font-size: 10.5pt; }
  main { max-width: none; padding: 0; }
  h2 { break-after: avoid; } h3 { break-after: avoid; }
  table, .pair, figure { break-inside: avoid; }
  a { color: var(--ink); }
}
"""

PAGE = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bridge: final project report</title><style>{CSS}</style></head>
<body><main>{SECTIONS}</main></body></html>"""

OUT.write_text(PAGE, encoding="utf-8")
print(f"wrote {OUT.relative_to(REPO)} ({OUT.stat().st_size / 1e6:.1f} MB)")

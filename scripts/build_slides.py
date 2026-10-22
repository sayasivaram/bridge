"""Build slides.html: a reveal.js deck (9 slides) in one file, images embedded, in the app's colors.

    python scripts/build_slides.py            # writes slides.html at the repo root

reveal.js loads from a CDN, so presenting needs an internet connection. Arrow keys move between
slides, F goes full screen, S opens the speaker notes. Cost numbers come from the same code as
scripts/cost_report.py; screenshots from docs/screenshots (embedded as compressed JPEGs via macOS
`sips` when available).
"""

from __future__ import annotations

import base64
import html
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from app import usage  # noqa: E402
from scripts import cost_report  # noqa: E402

SHOTS = REPO / "docs" / "screenshots"
STATIC = REPO / "static"
OUT = REPO / "slides.html"
REVEAL = "https://cdn.jsdelivr.net/npm/reveal.js@5.1.0"
_tmp = Path(tempfile.mkdtemp())


def img(path: Path, width: int = 1400, quality: int = 74) -> str:
    """Data URI for a screenshot, re-encoded as a smaller JPEG when possible (same as build_report.py)."""
    if shutil.which("sips"):
        out = _tmp / (path.stem + ".jpg")
        subprocess.run(["sips", "-s", "format", "jpeg", "-s", "formatOptions", str(quality), "-Z", str(width),
                        str(path), "--out", str(out)], check=True, capture_output=True)
        return "data:image/jpeg;base64," + base64.b64encode(out.read_bytes()).decode()
    return "data:image/png;base64," + base64.b64encode(path.read_bytes()).decode()


def svg(path: Path) -> str:
    return "data:image/svg+xml;base64," + base64.b64encode(path.read_bytes()).decode()


def money(x: float) -> str:
    return f"${x:,.2f}" if x >= 1 else f"${x:.4f}" if x >= 0.01 else f"${x:.5f}"


# --- numbers from the repo ---------------------------------------------------------------------

rows = cost_report.read_csv(cost_report.SAMPLE_CSV)
summary = usage.summarize(rows)
proj = cost_report.project(summary, dict(cost_report.ASSUMPTIONS), [1, 100, 10_000])
by_agent = {a["agent"]: a for a in summary["by_agent"]}
per_couple_month = 2 * proj["cost_per_user_month"]
cached_share = summary["total"]["cached_share"]

AGENTS = [("call_topics", "Call-Topic Agent"), ("ritual_builder", "Ritual Builder"), ("drift", "Drift Agent"),
          ("router", "Router classifier")]


def cost_chart() -> str:
    """Horizontal bars, one hue, direct labels: cost per run by agent."""
    data = [(label, by_agent[key]["avg_per_run"]["cost_usd"]) for key, label in AGENTS]
    top = max(v for _, v in data)
    w, bar_h, gap, left, right = 620, 34, 16, 190, 110
    h = len(data) * (bar_h + gap)
    parts = []
    for i, (label, v) in enumerate(data):
        y = i * (bar_h + gap) + 4
        bw = (w - left - right) * v / top
        parts.append(f'<text x="{left - 12}" y="{y + 23}" text-anchor="end" class="cl">{label}</text>'
                     f'<rect x="{left}" y="{y}" width="{bw:.1f}" height="{bar_h}" rx="6" fill="#d9674b"/>'
                     f'<text x="{left + bw + 10:.1f}" y="{y + 23}" class="cv">{money(v)}</text>')
    return f'<svg class="chart" viewBox="0 0 {w} {h}" role="img" aria-label="Cost per run by agent">{"".join(parts)}</svg>'


def shot(name: str, caption: str, width: int = 1400) -> str:
    return (f'<figure><div class="frame"><img src="{img(SHOTS / name, width)}" alt="{html.escape(caption)}"></div>'
            f"<figcaption>{caption}</figcaption></figure>")


def notes(text: str) -> str:
    return f'<aside class="notes">{text}</aside>'


MARK = svg(STATIC / "mark.svg")
LOGO = svg(STATIC / "logo.svg")
ICON = {k: svg(STATIC / "icons" / f"{k}.svg") for k in ("jar", "pulse", "rituals", "usage")}

# The app's pink stripes and hearts, for the title and closing slides
HEART = ("M12 21s-7.5-4.6-10-9.3C.3 8.4 2.2 4.5 6 4.5c2.2 0 3.6 1.3 6 3.6 2.4-2.3 3.8-3.6 6-3.6 3.8 0 5.7 3.9 "
         "4 7.2C19.5 16.4 12 21 12 21z")
HEARTS = ("url(\"data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='220' height='220'%3E%3Cdefs%3E"
          f"%3Cpath id='h' d='{HEART}'/%3E%3C/defs%3E"
          "%3Cuse href='%23h' transform='translate(24 30) rotate(-12 12 12)' fill='%23e07a5f' fill-opacity='.22'/%3E"
          "%3Cuse href='%23h' transform='translate(140 64) scale(.7) rotate(10 12 12)' fill='%239b7287' fill-opacity='.22'/%3E"
          "%3Cuse href='%23h' transform='translate(78 140) scale(1.15) rotate(8 12 12)' fill='%23e07a5f' fill-opacity='.16'/%3E"
          "%3Cuse href='%23h' transform='translate(176 168) scale(.6) rotate(-14 12 12)' fill='%234a7bb0' fill-opacity='.16'/%3E"
          "%3C/svg%3E\")")
STRIPES = f"{HEARTS}, repeating-linear-gradient(135deg, #fcecef 0 26px, #f8dde4 26px 52px)"


def kicker(n: int, text: str) -> str:
    return f'<div class="kicker"><img src="{MARK}" alt="">{n:02d} · {text}</div>'


# --- the slides --------------------------------------------------------------------------------

SLIDES = f"""
<section class="title-slide" data-background-gradient="{html.escape(STRIPES)}">
  <div class="title-wrap">
    <img class="logo" src="{LOGO}" alt="Bridge: stay connected, grow together">
    <div>
      <h1>Bridge</h1>
      <p class="pitch">Stay close between calls. Bridge turns the small moments of your week into real conversation,
        notices when you two start to drift, and suggests something to do together tonight.</p>
      <p class="byline"><b>Saya Sivaram</b> · AI Foundations (MGT 409) · Final project<br>
        <span class="muted">github.com/sayasivaram/bridge</span></p>
    </div>
  </div>
  {notes("Bridge is a multi-agent app for long-distance couples. It is not a scheduler or a generic chatbot: it works "
         "from what the couple actually lived this week.")}
</section>

<section>
  {kicker(1, "The problem")}
  <h2>Distance turns calls into logistics</h2>
  <div class="cols">
    <div class="card story">
      <p>I moved away from my partner to attend Yale for two years, and so did several of my classmates. Between
        recruiting, classes and clubs, the relationship can take the back seat. My partner travels abroad for months at a
        time (right now, Europe for six weeks), and we found ourselves simply connecting less.</p>
      <p class="muted">This is my life partner. I don't want to come out of these two years with a weaker relationship.</p>
    </div>
    <div class="stack">
      <div class="card point"><b>Calls loop on "how was your day?"</b> and logistics. The funny thing on the train or the
        small win at work is forgotten by call time.</div>
      <div class="card point"><b>Distance hides drift.</b> Five time zones apart, you don't notice the other pulling away
        until it surfaces as a fight.</div>
      <p class="who"><b>Who has it:</b> grad students like us, early-career professionals, military families, couples
        waiting on a visa.</p>
    </div>
  </div>
  {notes("Two problems: calls default to logistics, and drift is invisible at a distance. I have both, and so do many "
         "of my classmates.")}
</section>

<section>
  {kicker(2, "What is new")}
  <h2>Built from <em>this</em> couple's real week</h2>
  <table class="compare">
    <tr><th>Product</th><th>Uses the couple's own week?</th><th>Partner privacy</th><th>Notices drift</th></tr>
    <tr><td>Paired</td><td class="no">No: same questions for everyone</td><td>Answers revealed to each other</td><td class="no">No</td></tr>
    <tr><td>Lasting</td><td class="no">No: lessons and journaling</td><td>n/a</td><td class="no">No</td></tr>
    <tr><td>Couply</td><td>Partly: AI advice, not from shared moments</td><td>Unclear</td><td class="no">No</td></tr>
    <tr><td>Love Nudge</td><td class="no">No</td><td>n/a</td><td>A self-reported gauge</td></tr>
    <tr><td>ChatGPT</td><td>Only what one partner pastes</td><td>None: it sees what you paste</td><td class="no">No</td></tr>
    <tr class="us"><td>Bridge</td><td><b>Yes:</b> every starter cites the moment it came from</td>
      <td><b>Per item</b>, enforced in the database</td><td><b>Yes:</b> rules first, then one gentle suggestion</td></tr>
  </table>
  <div class="new3">
    <div class="card"><b>Grounded in your real week</b><span>starters come only from moments the couple logged, each checked in code</span></div>
    <div class="card"><b>Privacy as a feature</b><span>each moment and note is shared or private; the other partner's prompts never see it</span></div>
    <div class="card"><b>Drift, noticed early</b><span>plain rules run free every week; the model only writes one gentle suggestion</span></div>
  </div>
  {notes("Paired and Lasting give every couple the same content. ChatGPT only knows what one partner pastes in. Bridge "
         "is grounded in shared moments, keeps private things private, and notices drift.")}
</section>

<section>
  {kicker(3, "How the agents work")}
  <h2>Guard → router → agent → guard</h2>
  <div class="flow">
    <div class="node in"><b>You</b><span>page buttons, or the Ask Bridge chat</span></div>
    <div class="arrow">→</div>
    <div class="node guard"><b>Guard in</b><span>length, injection, crisis language, relationship decisions</span></div>
    <div class="arrow">→</div>
    <div class="node router"><b>Router</b><span>keyword rules ($0); an LLM classifier only when it's ambiguous</span></div>
    <div class="arrow">→</div>
    <div class="agents">
      <div class="node agent"><img src="{ICON['jar']}" alt=""><div><b>Call-Topic Agent</b><span>5 starters + a deeper question from shared moments</span></div></div>
      <div class="node agent"><img src="{ICON['pulse']}" alt=""><div><b>Drift Detector</b><span>rules first; the model only writes one nudge on drift</span></div></div>
      <div class="node agent"><img src="{ICON['rituals']}" alt=""><div><b>Ritual Builder</b><span>3 picks from a 25-ritual catalog, learns from ratings</span></div></div>
    </div>
    <div class="arrow">→</div>
    <div class="node guard"><b>Guard out</b><span>schema, coach boundary, then tokens + cost shown</span></div>
  </div>
  <div class="base">
    <div class="layer"><b>Privacy layer</b> · only <code>moments.py</code> and <code>pulses.py</code> read their tables; the
      rules live in SQL</div>
    <div class="layer"><b>llm.py</b> · gpt-6-luna via Portkey · daily token budget · 6-step limit · logs every call</div>
  </div>
  <p class="foot">Every agent: strict JSON schema, output validated in code against real ids, at most one retry, reasoning
    effort "low".</p>
  {notes("It's a hub, not a chain: agents don't call each other. Rules do the work wherever rules are enough, so clear "
         "messages and drift detection cost nothing.")}
</section>

<section>
  {kicker(4, "Demo")}
  <h2>What a couple sees</h2>
  <div class="shots">
    {shot("00-home.png", "Home: Ask Bridge and one bubble per feature", 1100)}
    {shot("12-call-prep-starters.png", "Prep for our call: each starter shows its moment")}
    {shot("15-pulse-drift-nudge.png", "Pulse: rules flag drift, one gentle nudge", 1100)}
    {shot("21-ritual-builder-picks.png", "Rituals: 3 picks for tonight's time and energy")}
  </div>
  {notes("Every screenshot is a real gpt-6-luna run on the seeded demo couple, Priya in New Haven and Leo in London.")}
</section>

<section>
  {kicker(5, "Cost to run")}
  <h2>About a penny per couple per month</h2>
  <div class="cols cost">
    <div class="stats">
      <div class="stat"><span class="num">{money(per_couple_month)}</span><span>per couple per month, expected</span></div>
      <div class="stat"><span class="num">{money(proj['users'][10_000])}</span><span>per month for 10,000 users</span></div>
      <div class="stat"><span class="num">${proj['ceiling_per_user_month']:.2f}</span><span>hard ceiling per user per month,
        from the daily token budget</span></div>
    </div>
    <div class="card">
      <h3>Cost per run, by agent</h3>
      {cost_chart()}
      <ul class="levers">
        <li><b>{cached_share:.0%}</b> of input tokens read from the prompt cache (stable prefix first)</li>
        <li><b>Reasoning effort "low":</b> 61% cheaper than the second prompt version, same quality</li>
        <li><b>Rules before models:</b> routing clear messages and detecting drift cost $0</li>
      </ul>
    </div>
  </div>
  {notes("Measured on real gpt-6-luna calls, logged in the app's llm_calls table and shown on its Usage page.")}
</section>

<section>
  {kicker(6, "Safety")}
  <h2>Two people's private feelings, defended</h2>
  <div class="defenses">
    <div class="card d"><b>Prompt injection</b><span>user text is escaped and tagged as data, never instructions</span></div>
    <div class="card d"><b>Partner privacy</b><span>private moments and notes filtered in SQL; a test fails if any other module reads them</span></div>
    <div class="card d"><b>No invented facts</b><span>every cited moment, ritual and rating is checked against the database</span></div>
    <div class="card d"><b>Token budget</b><span>50,000 tokens per user per day, refused before the model is called</span></div>
    <div class="card d"><b>Step limits</b><span>6 calls per request, at most one retry per agent</span></div>
    <div class="card d"><b>Coach boundary</b><span>no diagnosing or "break up"; crisis language gets helplines, not coaching</span></div>
  </div>
  <div class="proof">
    <img src="{img(SHOTS / '27-safety-demo-summary.png', 1600)}" alt="Safety demo summary: 7 of 7 defended">
    <img src="{img(SHOTS / '18-crisis-support-for-author.png', 1200)}" alt="Crisis language gets a private support card with helplines">
  </div>
  <p class="foot">Seven attacks and malfunctions in <code>scripts/run_safety_demo.py</code>, all defended · 166 automated tests pass</p>
  {notes("Each defense has a test. The safety demo plays a compromised model and shows the code-side checks catching it.")}
</section>

<section>
  {kicker(7, "Impact")}
  <h2>The numbers</h2>
  <div class="cols impact">
    <div class="stats">
      <div class="stat"><span class="num">0 / 25</span><span>starters that just retold a moment, down from 14 / 25 in the first prompt</span></div>
      <div class="stat"><span class="num">−35%</span><span>cost per Call-Topic run versus the first prompt</span></div>
      <div class="stat"><span class="num">$0.00081</span><span>for the real tester's whole session: six model calls</span></div>
    </div>
    <div class="card test">
      <h3>A real user test</h3>
      <p class="muted">A Gold Cohort classmate in a long-distance relationship; their partner is in grad school in Philadelphia.</p>
      <ul>
        <li><b>Would use it:</b> adding a moment and doing the Pulse check-in took very little effort</li>
        <li><b>Liked</b> how easy it was to move between tabs</li>
        <li><b>The feedback loop worked live:</b> the ritual they rated 5/5 topped the history for their next suggestion</li>
        <li><b>Concern:</b> trend lines over time might make a couple feel self-conscious</li>
      </ul>
    </div>
  </div>
  {notes("Quality numbers come from the automated 5-run evals; the user test is from the app's own logs plus the "
         "tester's feedback.")}
</section>

<section class="closing" data-background-gradient="{html.escape(STRIPES)}">
  {kicker(8, "What's next")}
  <h2>What the tester asked for, and the next month</h2>
  <div class="cols">
    <div class="card">
      <h3>From the user test</h3>
      <ul>
        <li><b>Calendar integration:</b> add planned rituals as calendar blocks, or let an agent check availability</li>
        <li><b>Ask Bridge as a coach:</b> reassurance and summaries ("what emotions did I report this week?"), inside the coach boundary</li>
        <li><b>Custom rituals</b> that couples add to the catalog the agent picks from</li>
        <li><b>A gentler trend view,</b> so a dip doesn't make a couple feel watched</li>
      </ul>
    </div>
    <div class="card">
      <h3>With another month</h3>
      <ul>
        <li><b>A real pilot:</b> 5–10 long-distance couples for three weeks</li>
        <li><b>Accounts and an invite flow,</b> with export and delete</li>
        <li><b>Time-zone-aware reminders</b> before a call, with its starters ready</li>
        <li><b>A second safety layer</b> behind the keyword crisis check</li>
      </ul>
    </div>
  </div>
  <p class="thanks"><img src="{MARK}" alt="">Thank you · <b>github.com/sayasivaram/bridge</b></p>
  {notes("The tester's suggestions shape the roadmap. The coach idea has to stay inside the same safety boundary.")}
</section>
"""

CSS = """
:root { --bg: #faf9f6; --card: #fff; --ink: #1e293b; --muted: #64748b; --line: #e7e2da; --primary: #2b4c7e;
  --primary-dark: #223d66; --blue: #4a7bb0; --blue-soft: #eaf0f7; --accent: #e07a5f; --accent-dark: #b5452b;
  --accent-soft: #fbe9e3; --orange: #f4a261; --mauve: #9b7287; --mauve-soft: #f3ecf0;
  --brand-gradient: linear-gradient(90deg, #4a7bb0, #9b7287, #e07a5f); }
.reveal-viewport { background: var(--bg); }
.reveal { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; font-size: 25px;
  color: var(--ink); }
.reveal .slides section { text-align: left; height: 100%; }
.reveal h1, .reveal h2, .reveal h3 { font-family: inherit; text-transform: none; color: var(--primary); letter-spacing: -0.01em; }
.reveal h1 { font-size: 3.4em; font-weight: 800; margin: 0 0 0.2em; }
.reveal h2 { font-size: 1.75em; font-weight: 800; margin: 0.1em 0 0.55em; }
.reveal h3 { font-size: 1em; font-weight: 700; margin: 0 0 0.5em; }
.reveal p { margin: 0 0 0.6em; line-height: 1.4; }
.reveal ul { margin: 0; padding-left: 1.1em; } .reveal li { margin: 0 0 0.45em; line-height: 1.35; }
.reveal code { font-size: 0.85em; background: var(--accent-soft); padding: 0 0.25em; border-radius: 4px; color: var(--accent-dark); }
.reveal em { color: var(--accent-dark); font-style: normal; }
.muted { color: var(--muted); }
.kicker { display: flex; align-items: center; gap: 10px; font-size: 0.62em; font-weight: 700; letter-spacing: 0.08em;
  text-transform: uppercase; color: var(--accent-dark); }
.kicker img { width: 30px; height: 30px; margin: 0; }
.kicker::after { content: ""; flex: 1; height: 3px; border-radius: 2px; background: var(--brand-gradient); margin-left: 8px; }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 18px; padding: 18px 22px;
  box-shadow: 0 4px 18px rgba(43, 76, 126, 0.07); }
.cols { display: grid; grid-template-columns: 1fr 1fr; gap: 26px; align-items: start; }
.stack { display: flex; flex-direction: column; gap: 16px; }
.foot { font-size: 0.68em; color: var(--muted); margin-top: 0.9em !important; }

/* 1 title */
.title-wrap { display: flex; align-items: center; gap: 56px; height: 100%; }
.title-wrap .logo { width: 360px; margin: 0; flex: none; background: rgba(255,255,255,.85); border-radius: 32px; padding: 18px;
  box-shadow: 0 10px 30px rgba(43, 76, 126, 0.12); }
.pitch { font-size: 1.15em; color: var(--ink); max-width: 700px; }
.byline { font-size: 0.8em; margin-top: 1.2em !important; }
.title-slide h1 { background: var(--brand-gradient); -webkit-background-clip: text; background-clip: text; color: transparent; }

/* 2 problem */
.story { background: linear-gradient(135deg, var(--primary-dark), #2e5288 60%, #33598f); color: #fff; border: 0;
  font-size: 0.95em; }
.story .muted { color: #dce6f2; font-style: italic; }
.point { font-size: 0.92em; border-left: 6px solid var(--accent); }
.who { font-size: 0.78em; color: var(--muted); }

/* 3 compare */
.compare { width: 100%; border-collapse: collapse; font-size: 0.86em; }
.reveal .compare th { text-align: left; color: var(--muted); font-size: 0.85em; text-transform: uppercase; letter-spacing: .04em;
  border-bottom: 2px solid var(--line); padding: 8px 10px; }
.reveal .compare td { padding: 11px 12px; border-bottom: 1px solid var(--line); vertical-align: top; }
.compare td:first-child { font-weight: 700; white-space: nowrap; }
.compare .no { color: var(--muted); }
.compare tr.us td { background: var(--accent-soft); color: var(--ink); border-bottom: 0; }
.compare tr.us td:first-child { color: var(--accent-dark); border-radius: 10px 0 0 10px; }
.compare tr.us td:last-child { border-radius: 0 10px 10px 0; }

.new3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; margin-top: 18px; }
.new3 .card { display: flex; flex-direction: column; gap: 4px; font-size: 0.74em; border-top: 5px solid var(--accent); padding: 14px 18px; }
.new3 b { color: var(--primary); font-size: 1.1em; } .new3 span { color: var(--muted); line-height: 1.3; }

/* 4 flow */
.flow { display: flex; align-items: center; gap: 10px; font-size: 0.76em; }
.flow .arrow { color: var(--accent); font-size: 1.9em; font-weight: 800; }
.node { background: var(--card); border: 1px solid var(--line); border-radius: 14px; padding: 16px 16px; flex: 1;
  box-shadow: 0 4px 14px rgba(43, 76, 126, 0.07); display: flex; flex-direction: column; gap: 4px; }
.node b { color: var(--primary); font-size: 1.1em; } .node span { color: var(--muted); line-height: 1.3; }
.node.in { background: var(--blue-soft); } .node.guard { border-top: 5px solid #3f8f6b; } .node.router { border-top: 5px solid var(--blue); }
.agents { display: flex; flex-direction: column; gap: 8px; flex: 1.7; }
.node.agent { flex-direction: row; align-items: center; gap: 10px; border-left: 5px solid var(--accent); padding: 8px 12px; }
.node.agent img { width: 52px; height: 52px; margin: 0; flex: none; }
.node.agent div { display: flex; flex-direction: column; gap: 2px; }
.base { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; margin-top: 26px; font-size: 0.76em; }
.layer { background: var(--mauve-soft); border-radius: 12px; padding: 10px 14px; border: 1px dashed var(--mauve); }

/* 5 shots */
.shots { display: grid; grid-template-columns: 1fr 1fr; gap: 14px 24px; align-items: start; }
.shots figure { margin: 0; }
.shots .frame { height: 236px; background: var(--card); border: 1px solid var(--line); border-radius: 14px;
  box-shadow: 0 4px 14px rgba(43, 76, 126, 0.08); overflow: hidden; }
.shots img { width: 100%; height: 100%; object-fit: cover; object-position: top; margin: 0; display: block; }
.shots figcaption { font-size: 0.66em; color: var(--muted); margin-top: 6px; line-height: 1.3; }

/* 6 cost + 8 impact */
.stats { display: flex; flex-direction: column; gap: 14px; }
.stat { background: var(--card); border: 1px solid var(--line); border-radius: 16px; padding: 14px 20px;
  border-left: 6px solid var(--accent); display: flex; flex-direction: column; }
.stat .num { font-size: 1.7em; font-weight: 800; color: var(--primary); line-height: 1.1; }
.stat span:last-child { font-size: 0.78em; color: var(--muted); }
.chart { width: 100%; height: auto; }
.chart .cl { font-size: 17px; fill: var(--ink); } .chart .cv { font-size: 17px; fill: var(--ink); font-weight: 700; }
.levers { font-size: 0.7em; margin-top: 12px !important; }
.test { font-size: 0.92em; } .test .muted { font-size: 0.85em; }

/* 7 safety */
.defenses { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; }
.card.d { padding: 12px 16px; display: flex; flex-direction: column; gap: 4px; font-size: 0.72em; border-top: 5px solid #3f8f6b; }
.card.d b { color: var(--primary); font-size: 1.12em; } .card.d span { color: var(--muted); line-height: 1.3; }
.proof { display: flex; gap: 16px; margin-top: 14px; } .proof img { height: 205px; width: auto; margin: 0; border-radius: 12px;
  border: 1px solid var(--line); box-shadow: 0 4px 14px rgba(43, 76, 126, 0.08); }

/* 9 closing */
.closing .card { font-size: 0.88em; }
.thanks { display: flex; align-items: center; gap: 12px; margin-top: 1em !important; font-size: 0.9em; color: var(--primary); }
.thanks img { width: 40px; height: 40px; margin: 0; }
.reveal .slide-number { background: transparent; color: var(--muted); font-size: 14px; }
.reveal .controls { color: var(--accent); }
.reveal .progress { color: var(--accent); }
"""

PAGE = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Bridge · Saya Sivaram · MGT 409 final project</title>
<link rel="icon" href="{MARK}">
<link rel="stylesheet" href="{REVEAL}/dist/reset.css">
<link rel="stylesheet" href="{REVEAL}/dist/reveal.css">
<link rel="stylesheet" href="{REVEAL}/dist/theme/white.css">
<style>{CSS}</style>
</head>
<body>
<div class="reveal"><div class="slides">
{SLIDES}
</div></div>
<script src="{REVEAL}/dist/reveal.js"></script>
<script src="{REVEAL}/plugin/notes/notes.js"></script>
<script>
Reveal.initialize({{ hash: true, slideNumber: "c/t", width: 1280, height: 720, margin: 0.05, center: false,
  transition: "fade", plugins: [RevealNotes] }});
</script>
</body>
</html>
"""

if __name__ == "__main__":
    OUT.write_text(PAGE, encoding="utf-8")
    print(f"wrote {OUT.relative_to(REPO)} ({OUT.stat().st_size / 1e6:.1f} MB, {SLIDES.count('<section')} slides)")

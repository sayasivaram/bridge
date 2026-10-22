# Closer

A multi-agent app that helps long-distance couples stay close between calls:

- a **Moment Jar** where each partner drops small moments from their week, which a Call-Topic
  Agent turns into conversation starters,
- a 30-second weekly **Connection Pulse**, where simple rules spot drift and a Drift Agent
  suggests one gentle fix,
- a **Ritual Builder** that picks shared activities for tonight and learns from your ratings,
- an **Ask Closer** chat box that routes a question to the right agent.

The agents use gpt-6-luna through Portkey. They sit on a privacy layer that keeps each partner's
private moments and notes away from the other partner, and away from any prompt whose output the
other partner sees. Architecture: [docs/architecture.md](docs/architecture.md).

## Setup (about 2 minutes)

You need **Python 3.11 or newer** (`python3 --version` to check; developed on 3.14) and git.
Nothing else: no database server, no Node, no Docker. SQLite comes with Python.

### 1. Clone the repo

```bash
git clone <this-repo-url> closer
cd closer
```

### 2. Create a virtual environment

macOS / Linux:
```bash
python3 -m venv .venv
source .venv/bin/activate
```

Windows (PowerShell):
```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
```
If PowerShell says running scripts is disabled, run
`Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first (this window only).
On Ubuntu/Debian, if `venv` is missing: `sudo apt install python3-venv`.

### 3. Install the dependencies

```bash
pip install -r requirements.txt
```

### 4. Put your `.env` one folder up

Closer looks for a `.env` file in two places, in this order:

1. this repo's folder (`closer/.env`)
2. the folder **one level above** the repo (`../.env`)

The usual setup is a `.env` in the folder that contains `closer/`:

```
your-folder/
├── .env          <- PORTKEY_API_KEY=... (and/or OPENAI_API_KEY=...)
└── closer/       <- this repo
```

If you don't have one yet, copy the example and fill in a key (`.env.example` lists the two
supported names):

```bash
cp .env.example ../.env
```

Supported keys, spelled exactly: `PORTKEY_API_KEY` (course key, preferred) or `OPENAI_API_KEY`.
The model is always `gpt-6-luna`. A key exported in your shell (e.g. in `~/.zshrc`) overrides
both `.env` files. Never commit a real `.env` (it is in `.gitignore`).

### 5. Load the demo data

```bash
python -m app.seed
```

This creates `data/closer.db` with a demo couple: Priya in New Haven and Leo in London, with three
weeks of moments, weekly check-ins, and rituals. (Step 6 also does this automatically if you skip
it.) To start over later: `python -m app.seed --reset`.

### 6. Run it

```bash
python run.py
```

Open the address it prints, normally <http://127.0.0.1:8000>. It says which key it found and
where. If port 8000 is busy, it picks the next free port and tells you
(`python run.py --port 9000` to choose one). If no key is found, it stops and lists the exact
places it looked.

## Try this first (2 minutes)

1. **Moment Jar** (home page): read this week's moments as Priya, then switch **Viewing as** to
   Leo (top right). His private moment ("Still behind at work…") shows only in his jar.
2. Press **Prep for our call**: 5 conversation starters, each with the moment it came from,
   plus a token and cost badge.
3. In **Ask Closer**, try "What should we do tonight?" and open **How this was handled** to see
   the guard, router, and agent steps.
4. **Pulse** tab: the trend chart and this week's nudge (the rules found drift: Leo's score fell
   4 → 3 → 2).
5. **Rituals** tab: pick a time and energy level, press **Suggest 3 rituals**, plan one, mark it
   done, and rate it.
6. **Usage** tab (or <http://127.0.0.1:8000/usage>): tokens and cost per agent, per feature, and
   per partner, from every logged model call.

## Costs

The Usage tab shows what this install has spent. To project costs:

```bash
python scripts/cost_report.py
```

It prints average tokens and cost per run for each agent (from `data/closer.db`, or the measured
sample in `docs/usage/measured_calls.csv` on a fresh install) and monthly cost for 1, 100, and
10,000 users. The runs-per-user-per-week assumptions are at the top of the script, or override
them: `python scripts/cost_report.py --per-week call_topics=3 ritual_builder=1`.
`--measure 3` re-measures every agent live (about $0.006).

## Tests and safety demo

```bash
pytest
```

The tests use a fake model, so they spend no tokens. To also run the live check against
gpt-6-luna (a malicious moment the real model must ignore, about $0.001):

```bash
CLOSER_LIVE=1 pytest -k live -s
```

(PowerShell: `$env:CLOSER_LIVE=1; pytest -k live -s`)

Seven attacks and malfunctions, run against the real app code with readable results:

```bash
python scripts/run_safety_demo.py          # scripted model that plays a compromised model (free)
python scripts/run_safety_demo.py --live   # cases 1, 2 and 7 against the real gpt-6-luna (~$0.001)
```

1. prompt injection in a moment · 2. prompt injection in a pulse note · 3. trying to read the
partner's private moment · 4. an agent citing a moment that doesn't exist · 5. a distress
message · 6. going over the daily token budget · 7. trying to make the coach say "you should
break up". The same scenarios are tests in `tests/test_safety.py`.

## Project layout

```
app/
  config.py        finds the API key (.env in repo, then one folder up), limits
  llm.py           the one model wrapper: gpt-6-luna via Portkey, token counts, cost, logging,
                   per-user daily token budget, per-request step limit
  agents/          call_topics.py (Call-Topic Agent), drift.py (Drift Detector),
                   ritual_builder.py (Ritual Builder)
  orchestrator.py  chat requests: guard -> router -> agent -> guard, logged to agent_runs
  router.py        keyword routing, LLM classifier only when ambiguous
  guard.py         input checks (length, injection, crisis, relationship decisions) and output
                   checks (schema, coach boundary)
  moments.py       Moment Jar data; the partner-privacy rule lives in its SQL
  pulses.py        Pulse data; the "see scores once both checked in" rule lives in its SQL
  rituals.py       ritual catalog, the couple's rituals and ratings (the feedback loop)
  usage.py         token and cost summaries from llm_calls (Usage tab, cost report)
  safety.py        crisis pre-check and support messages
  timeutil.py      each partner's local "today" / "this week" (timestamps are stored in UTC)
  prompting.py     wraps user text in escaped tags before it goes into any prompt
  schema.sql       database tables;  db.py: SQLite helpers;  seed.py: demo couple;  main.py: routes
data/
  seed_couple.json     demo couple (edit freely)
  ritual_catalog.json  the 25 starter rituals the Ritual Builder picks from
  prices.json          gpt-6-luna prices per 1M tokens
  closer.db            created by step 5 (not committed);  uploads/: moment photos (not committed)
docs/              architecture diagrams, eval results, measured usage, screenshots
scripts/           cost_report.py, run_safety_demo.py, eval_call_topics.py
static/            single-page frontend (HTML, CSS, vanilla JS)
tests/             pytest tests (fake model client, no tokens spent)
run.py             starts the server
```

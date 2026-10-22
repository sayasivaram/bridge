# Closer

A multi-agent app that helps long-distance couples stay connected between calls:
a **Moment Jar** that turns small moments from the week into call topics, a weekly
**Connection Pulse** that notices drift, and a **Ritual Builder** that suggests shared
activities. Three AI agents (gpt-6-luna through Portkey) sit on a privacy layer that keeps each
partner's private moments and notes away from the other partner, and away from any prompt
whose output the other partner sees.

## Run it locally (about 2 minutes)

You need **Python 3.11 or newer** (`python3 --version` to check; developed on 3.14). Nothing else:
no database server, no Node, no Docker. SQLite comes with Python.

1. **Clone and enter the repo**
   ```bash
   git clone <this-repo-url> closer
   cd closer
   ```

2. **Create a virtual environment and install dependencies**

   macOS / Linux:
   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

   Windows (PowerShell):
   ```powershell
   py -m venv .venv
   .venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
   If PowerShell says running scripts is disabled, run
   `Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass` first (this window only).
   On Ubuntu/Debian, if `venv` is missing: `sudo apt install python3-venv`.

3. **Add an API key.** The app looks for a `.env` file in two places, in this order:
   1. this repo's folder (`closer/.env`)
   2. the folder one level above the repo (`../.env`)

   If your `.env` is already in the folder above, skip this step. Otherwise copy the example
   and fill in a key:
   ```bash
   cp .env.example .env
   ```
   Supported keys: `PORTKEY_API_KEY` (course key, preferred) or `OPENAI_API_KEY`.
   The model is always `gpt-6-luna`. A key exported in your shell (e.g. in `~/.zshrc`)
   overrides both `.env` files.

4. **Start the app**
   ```bash
   python run.py
   ```
   Open the address it prints, normally <http://127.0.0.1:8000>. If port 8000 is busy (another
   app is running), it picks the next free port and tells you; `python run.py --port 9000`
   chooses one. On first start it creates `data/closer.db` and loads a demo couple (Priya in New
   Haven and Leo in London) with three weeks of moments, weekly check-ins, and rituals.
   The line at the bottom of the page says which key was found and where it came from. If no
   key is found, `run.py` stops and lists the exact places it looked.

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

## What you can do

- **Moment Jar:** add small moments (text, a mood, an optional photo or song), shared with your
  partner or private. Switch partners with **Viewing as** to see the other side.
- **Prep for our call:** the Call-Topic Agent turns the last 7 days of *shared* moments into 5
  conversation starters (each showing the moment it came from) plus one deeper question. The
  badge shows that run's tokens and cost; "How this was made" shows each step.
- **Pulse:** a 30-second weekly check-in (connection 1-5, amount of contact, an optional note you
  can share or keep private). You see your partner's score once you've both checked in. The
  Drift Detector runs simple rules first and calls the AI only when they flag drift, then shows
  one gentle suggestion as "this week's nudge". Notes that suggest abuse, self-harm, or serious
  distress skip coaching and show the author support lines instead.
- **Rituals:** say how much time and energy you have tonight and the Ritual Builder picks 3
  rituals from a catalog of 25 (`data/ritual_catalog.json`), explaining why each fits you two
  based on how you rated past rituals. Plan one, mark it done, and both rate it 1-5; your
  favorites and least favorites shape the next suggestions.

- **Ask Closer:** the chat box on the home page sends your message through a guard, a router
  (keyword rules first, an LLM classifier only when unclear), the right agent, and an output
  guard. Each answer shows which agent handled it, its token cost, and the full trace.

See [docs/architecture.md](docs/architecture.md) for the architecture diagrams.

## Reset the demo data

```bash
python -m app.seed --reset
```

## Run the tests

```bash
pytest
```

The tests use a fake model, so they spend no tokens. To also run the live check against
gpt-6-luna (a malicious moment the real model must ignore, about $0.001):

```bash
CLOSER_LIVE=1 pytest -k live -s
```

(PowerShell: `$env:CLOSER_LIVE=1; pytest -k live -s`)

## Project layout

```
app/
  config.py    finds the API key (.env in repo, then one folder up), limits
  llm.py       the one model wrapper: gpt-6-luna via Portkey, token counts, cost,
               logging, per-user daily token budget, per-request step limit
  agents/      AI agents (call_topics.py: Call-Topic Agent; drift.py: Drift Detector;
               ritual_builder.py: Ritual Builder)
  orchestrator.py  chat requests: guard -> router -> agent -> guard, logged to agent_runs
  router.py    keyword routing, LLM classifier only when ambiguous
  guard.py     input checks (length, injection, crisis) and output checks (schema, coach boundary)
  moments.py   Moment Jar data access; the partner-privacy rule lives in its SQL
  pulses.py    Pulse data access; the "see scores once both checked in" rule lives in its SQL
  rituals.py   ritual catalog, the couple's rituals and ratings (the feedback loop)
  safety.py    crisis pre-check (keyword patterns) and support messages
  timeutil.py  each partner's local "today" / "this week" (timestamps are stored in UTC)
  prompting.py wraps user text in escaped tags before it goes into any prompt
  schema.sql   database tables
  db.py        SQLite helpers
  seed.py      loads the demo couple
  main.py      FastAPI routes
data/
  seed_couple.json   demo couple (edit freely)
  ritual_catalog.json  the 25 starter rituals the Ritual Builder picks from
  prices.json        gpt-6-luna prices per 1M tokens
  closer.db          created on first run (not committed)
  uploads/           moment photos (not committed)
static/      single-page frontend (HTML, CSS, vanilla JS)
tests/       pytest tests (use a fake model client, so no tokens are spent)
run.py       starts the server
```

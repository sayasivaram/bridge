# Closer

A multi-agent app that helps long-distance couples stay connected between calls:
a **Moment Jar** that turns small moments from the week into call topics, a weekly
**Connection Pulse** that notices drift, and a **Ritual Builder** that suggests shared
activities. (Work in progress.)

## Run it locally (about 5 minutes)

You need **Python 3.11 or newer** (`python3 --version` to check).

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
   Open <http://127.0.0.1:8000>. On first start the app creates `data/closer.db` and loads a
   demo couple (Priya in New Haven and Leo in London) with three weeks of moments, weekly
   pulses, and rituals. Use the **Viewing as** dropdown (top right) to switch partners.
   The status box says whether a key was found and where it came from. If no key is found,
   `run.py` stops and lists the exact places it looked.

## Reset the demo data

```bash
python -m app.seed --reset
```

## Run the tests

```bash
pytest
```

## Project layout

```
app/
  config.py    finds the API key (.env in repo, then one folder up), limits
  llm.py       the one model wrapper: gpt-6-luna via Portkey, token counts, cost,
               logging, per-user daily token budget, per-request step limit
  moments.py   Moment Jar data access; the partner-privacy rule lives in its SQL
  prompting.py wraps user text in escaped tags before it goes into any prompt
  schema.sql   database tables
  db.py        SQLite helpers
  seed.py      loads the demo couple
  main.py      FastAPI routes
data/
  seed_couple.json   demo couple (edit freely)
  prices.json        gpt-6-luna prices per 1M tokens
  closer.db          created on first run (not committed)
  uploads/           moment photos (not committed)
static/      single-page frontend (HTML, CSS, vanilla JS)
tests/       pytest tests (use a fake model client, so no tokens are spent)
run.py       starts the server
```

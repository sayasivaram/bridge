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
   Open <http://127.0.0.1:8000>. The status box on the home page says whether a key was
   found and which `.env` it came from.

## Run the tests

```bash
pytest
```

## Project layout

```
app/        FastAPI backend (config.py loads .env; main.py defines the API)
static/     single-page frontend (HTML, CSS, vanilla JS)
tests/      pytest tests
run.py      starts the server
```

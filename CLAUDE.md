# CLAUDE.md — "Closer" (AI Foundations final project)

## What this is

**Closer** (working name) is a multi-agent app for long-distance couples. It is **not a scheduler**.
It helps a couple stay emotionally connected between calls. Solo university final project, graded
on novelty, a working app, a 10–15 page report, and a ≤10-slide deck.

## The three features

1. **Moment Jar + Call-Topic Agent.** Each partner drops small moments from their week (funny
   things, songs, worries, wins). Before a call, the agent turns the *real* moments into
   conversation starters so calls skip the "how was your day?" loop.
2. **Connection Pulse.** A 30-second weekly check-in: each partner rates how connected they feel
   (1–5) plus an optional note. An agent detects drift (low or falling scores, a mismatch between
   partners, fewer moments logged) and suggests **one** specific fix.
3. **Ritual Builder.** Suggests shared activities (cook the same recipe on video, co-watch, a
   2-player game, letter swap) based on past ratings, time available, and energy level, and learns
   from feedback on rituals they have tried.

## Hard requirements (do not break these)

### Setup and running
- Local app that runs **from a fresh clone in under 10 minutes** by following the README
  (the 10-minute rule: not running after 10 min = 0 points for "staff can run it").
- Stack: **Python 3.11+, FastAPI, SQLite, single-page frontend in plain HTML/CSS/vanilla JS.**
  No Node build step, no Docker, no extra services. Keep dependencies minimal.
- README must give step-by-step run instructions and list every key needed.

### API keys and model
- Load `.env` from **the repo folder first, then the folder one level above the repo**.
  The graders clone every repo into `submissions/` with their `.env` at `submissions/.env`.
  Test this by moving `.env` up one folder.
- Key names must be **exactly** `PORTKEY_API_KEY` or `OPENAI_API_KEY`. No other key names.
- Course key (Portkey) → model **`gpt-6-luna` only**. Never fall back to any other model on the
  course key. Do not copy model names from older homework `.env` files (they say `gpt-5.6-luna`).
- Ship `.env.example` with key names and no real values. **Never commit `.env`** or any real key.

### Demo data
- Seed data: one fake couple with about **3 weeks** of moments, pulses, and rituals, so the app
  is demoable immediately after setup. One command loads it.

### Token usage visible in the UI
- Every agent run shows **input tokens, cached input tokens, output tokens, and estimated cost**
  in the UI. Also log them (DB table) so the report can quote real numbers per run, per user per
  month, and at 100 / 10,000 users.

### Safety (graded, each defense needs a test)
Each defense below gets a simple automated test that shows it working:
1. **Prompt injection defense** on all user-entered text (moments, notes, feedback). User text is
   data, wrapped/delimited, never treated as instructions.
2. **Privacy between partners.** One partner's private content is never shown to or quoted for the
   other partner unless it was shared.
3. **No invented facts.** Agents only reference moments/pulses/rituals that exist in the DB;
   outputs are checked against source IDs.
4. **Per-user token budget.** A bug or bad actor cannot burn through tokens; requests over budget
   are refused before calling the model.
5. **Loop/step limits.** Every agent run has a hard cap on steps/calls.
6. **Relationship-coach boundary.** No diagnosing, no "you should break up". If someone describes
   abuse or real distress, respond gently and refer them to a professional / hotline instead of
   coaching.

### Prompt caching
- Build prompts as **stable content first** (system prompt, safety rules, couple profile), and
  **variable content last** (this week's moments, the user's request). Keep the stable prefix
  byte-identical across runs so cached input tokens show up in the usage numbers.

## Working style
- Small steps. **Commit after each step.** Explain what changed after each step.
- Write a simple test for every safety defense (pytest).
- Don't write ahead of the current step; the user drives the order.
- Screenshot-worthy states (good runs, defenses firing, token panels) should be easy to reproduce
  from seed data, since the report and slides need them.

## How it is graded (keep these in mind when making choices)

100 points total:
- **Idea, 21:** novel, solves a real problem, could actually be used. Overdone ideas (chat with
  PDFs, trip planner, study buddy, etc.) are capped at Tier 1; Closer must stay clearly *not* a
  generic scheduler or chatbot.
- **App, 30:** it works end to end with real agents/tools/data (15), staff can run it from a fresh
  clone with `.env` one folder up (5), looks good and is easy to use (10).
- **Report, 41:** motivation + what is new (8), agent design (10), tokenomics (8), safety (8),
  impact + metrics (5), design (2).
- **Slides, 8:** content (6), design (2).

**Evidence earns full credit.** A report section without its evidence gets at most half points:
- What is new → named existing products compared against (e.g. Paired, Lasting, Between, Couply).
- Agent design → one real run traced step by step with screenshots of what each agent did.
- Tokenomics → real token counts from runs, shown in logs or the app's own usage display.
- Safety → screenshot or test of a defense actually stopping an attack or malfunction.
- Impact → a real user tried it (quote, screenshot, before/after time).

Tier 3 needs a real user trying it and measured improvement versus how couples do it today.

## Deliverables (all submitted on Canvas)
- **GitHub repo link:** all code, README, `.env.example`. Public, or private with `zlisto` added
  as a collaborator.
- **`report.html`:** single self-contained HTML file, 10–15 printed pages, images embedded
  (base64) so it opens by double-click anywhere. Sections in order: 1 Title + links,
  2 Motivation, 3 What is new, 4 The agents (diagram, tools, models, one real run),
  5 Tokenomics, 6 Safety, 7 Impact, 8 Limits + next steps.
- **`slides.html`:** reveal.js from a CDN, ≤10 slides, images embedded, same colors as the app.
  Order: title, problem, what is new, agents diagram, demo screenshots, cost, safety, impact,
  what's next.
- **Demo video (optional):** 2–3 min screen recording, a backup if the app won't run for staff.

## Open questions (resolve as we go)
- Exact Portkey call pattern (base URL / headers) and gpt-6-luna per-token prices for cost
  estimates.
- Which key wins if both `PORTKEY_API_KEY` and `OPENAI_API_KEY` are present (default plan:
  prefer Portkey + gpt-6-luna).
- Agent architecture (proposed: a guard step for input safety, the three feature agents, and a
  shared output checker for grounding/privacy/coach boundary).

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
  in the UI (plus cache-write tokens, which are billed separately; see "Prices" below). Also log them (DB table) so the report can quote real numbers per run, per user per
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

## Code conventions for privacy and untrusted text (keep these as features are added)
- **Only `app/moments.py` reads `moments` and only `app/pulses.py` reads `pulses`.** Their SQL
  applies the privacy rules (private moments; pulse reveal rule: partner's score once both
  checked in, note only if shared). Tests fail if any other module queries those tables.
- **Anything shown to both partners uses only data both can see** (shared moments, revealed weeks,
  shared notes; moment COUNTS are shared-only too, or they'd reveal private moments exist).
- **Crisis pre-check (`app/safety.py`) runs before any coaching model call**, on private notes too.
  A match: support resources to the author only; the partner sees the same quiet card as a
  normal week. Flagged text is never sent to a model.
- **Agents live in `app/agents/`** (one module each) and call the model only via `app/llm.py`.
  Each has a stable `SYSTEM_PROMPT`, validates model output in code, and retries at most once.
- **Agents get moments only via `moments.for_prompt(..., output_for=...)`.** Use the id of the
  partner who will see the output, or `moments.BOTH` when both will see it (shared only).
- **User text goes into prompts only via `app/prompting.py`** (`wrap` / `moments_block`): it is
  HTML-escaped and tagged. `UNTRUSTED_DATA_RULES` goes in every agent's stable system prompt.
- **Ritual Builder picks only catalog ids** (`data/ritual_catalog.json`), filtered to tonight's
  time/energy in code; validation drops unknown or non-fitting ids and invented purchases or
  bookings. Ritual feedback notes are visible to both partners unless the crisis pre-check flags
  them (then hidden from the partner and never sent to a model).
- The frontend renders user text with `textContent`, never `innerHTML`.
- **Time zones:** timestamps are stored in UTC; "today" / "this week" / agent windows are in the
  viewing or requesting partner's `users.timezone`. Use `app/timeutil.py` (`local_today`,
  `utc_bounds`), never `date.today()` or `date(created_at)` in SQL. Only the token budget day is
  UTC on purpose (its message says so). `tzdata` is in requirements for Windows.

## Calling gpt-6-luna through Portkey (same setup as Homework 5 and 6)
- OpenAI SDK pointed at Portkey: `base_url="https://api.portkey.ai/v1"`, `api_key=<key>`, and
  header `x-portkey-api-key: <key>`. Model `"gpt-6-luna"`.
- Use the **Responses API** (`/v1/responses`). Portkey's gpt-6-luna rejects function tools on
  `/v1/chat/completions` while reasoning is on.
- Responses report the served model as `gpt-6-luna`, `gpt-6-luna-global`, or a dated snapshot
  (`gpt-6-luna-2026-09-22`). Accept only those; fail loudly on anything else.
- If both keys are present, Portkey wins (already built in `app/config.py`).
- SDK: plain `openai` SDK (no pydantic-ai). Every agent calls `app/llm.py`; nothing else
  talks to the model.

## Prices and cost accounting
Prices live in `data/prices.json` (gpt-6-luna, per 1M tokens, from MGT 409 Lecture 11):

| Token type | $ / 1M |
|---|---|
| Plain (uncached) input | 0.10 |
| Cached input (cache read) | 0.01 |
| Cache write | 0.125 |
| Output (includes reasoning) | 0.50 |

- Cost = tokens × price ÷ 1,000,000. The API's `input_tokens` **includes** cached and
  cache-write tokens, so `uncached = input − cached − cache_write`.
- Log and display all four counts (input, cached input, cache write, output) per run, plus
  reasoning tokens for reference. Cache write must be shown: it is billed separately.

## What Homework 6 measured about caching on this deployment (design around it)
- The provider behind Portkey caches the **longest run of messages that exactly match an earlier
  call**; it does not honor an explicit breakpoint by itself.
- Nearly all input that isn't read from the cache is billed as a **cache write** ($0.125/1M),
  which is *more* than plain input. So the variable tail of each prompt costs extra, every call.
- Tool definitions are sent before the instructions, so they are part of the cached opening.
  Keep tool schemas stable.
- Implications for Closer: keep the stable prefix (tools, system prompt, safety rules, couple
  profile) byte-identical and in front; keep the variable tail (this week's moments, the request)
  as short as possible; avoid extra model calls that resend the whole prefix.

## Measured on this deployment while building the Call-Topic Agent (Oct 21)
- **Portkey has an exact-match response cache** on the course key. An identical request comes back
  with `x-portkey-cache-status: HIT`, the same response id and the original usage numbers, but
  no model call. `app/llm.py` reads that header: a HIT costs $0 and doesn't count toward the
  daily budget (`llm_calls.gateway_cache`). Side effect: pressing "Prep" twice with no new
  moments returns the same starters.
- **Prompt caching works**: the Call-Topic stable prefix (system prompt + profile, ~1,470 tokens)
  is read from cache on later calls, by either partner (1,467 of ~1,900 input tokens).
- **Reasoning dominates output**: 240 to 1,200 reasoning tokens per run (40-75% of output).
  Per-run cost varied $0.00038-$0.00091 for the same prompt. Lowering reasoning effort is the
  obvious cost lever to test (and check it doesn't hurt starter quality).
- Strict JSON-schema output (`text.format` json_schema) works on the Responses API.
- **Prompt v2 vs v1** (5 runs each, `docs/evals/call_topics_compare_baseline_v2.md`): retold
  starters 14/25 -> 0/25, partner-focused starters in Priya's runs 3/15 -> 6/15, perspective
  slips fixed; but mean cost $0.00065 -> $0.00107 because reasoning doubled (734 -> 1,576), and
  one call spent all 2,500 output tokens on reasoning (empty answer, recovered by the retry).
  v2 also over-copies the example's "Remind me to tell you" (3/25).
- **v3 = v2 + reasoning effort "low" + a non-reusable example teaser** (current): mean cost
  $0.00042 (-35% vs v1, -61% vs v2), latency 8s, 0 retries, 0/25 retold, 0/25 copied from the
  example. Slight slips: 1 of 3 Priya runs had only 1 Leo-focused starter; a phrase repeats
  across runs. gpt-6-luna efforts: none, low, medium (default), high, xhigh, max ("minimal" is
  rejected). Comparison: `docs/evals/call_topics_compare_baseline_v2_v3.md`.
- **Drift Agent** (live, seed couple): 1,553 input tokens, ~110 output, 0 reasoning at low effort;
  $0.00025 cold, $0.00007 with the prompt cached. Rules-only weeks cost $0. Nudges are cached
  per couple + signals, so both partners share one call.
- **Ritual Builder** (live, 4 runs): ~2,850 input tokens with 2,465 cached (the catalog is in the
  stable prefix), 230-550 output; $0.00015-0.00035 per run. A too-broad "invented numbers"
  check once rejected "10 minutes" and forced a retry ($0.00075); it now checks ratings only.
- After any prompt change: `python scripts/eval_call_topics.py --label <name>`, then
  `--compare baseline v3 <name>` (about $0.002-0.005 per 5-run eval).

## Open questions (resolve as we go)
- Agent architecture (proposed: a guard step for input safety, the three feature agents, and a
  shared output checker for grounding/privacy/coach boundary).

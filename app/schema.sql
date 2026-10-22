-- Closer database schema (SQLite).
-- One couple, two partners. Every row a partner writes records its author, so the
-- app can enforce privacy: a partner's private content is never shown to the other.

PRAGMA foreign_keys = ON;

-- The couple and the stable profile that goes at the start of every prompt (cacheable).
CREATE TABLE IF NOT EXISTS couples (
    id              INTEGER PRIMARY KEY,
    name            TEXT NOT NULL,              -- e.g. "Priya & Leo"
    together_since  TEXT,                       -- ISO date
    apart_since     TEXT,                       -- ISO date the long distance started
    profile         TEXT NOT NULL DEFAULT '',   -- short shared description: interests, call habits
    inside_jokes    TEXT NOT NULL DEFAULT '',   -- one per line; both partners can see these
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY,
    couple_id           INTEGER NOT NULL REFERENCES couples(id),
    role                TEXT NOT NULL CHECK (role IN ('A', 'B')),  -- Partner A / Partner B
    name                TEXT NOT NULL,
    city                TEXT,
    timezone            TEXT,                   -- IANA name, e.g. "America/New_York"
    shared_stressors    TEXT NOT NULL DEFAULT '',  -- what this partner CHOSE to share they're stressed about
    daily_token_budget  INTEGER,                -- NULL = use the app default
    created_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (couple_id, role)
);

-- Moment Jar: small things from the week. Read ONLY through app/moments.py, which applies
-- the privacy rule in every query:
-- visibility 'shared'  = partner can see it; agents may use it for either partner.
-- visibility 'private' = only the author sees it; agents use it only for output to the author.
-- A moment has at most one attachment: a song (free text) or an image (file in data/uploads/).
CREATE TABLE IF NOT EXISTS moments (
    id          INTEGER PRIMARY KEY,
    couple_id   INTEGER NOT NULL REFERENCES couples(id),
    author_id   INTEGER NOT NULL REFERENCES users(id),
    mood        TEXT NOT NULL CHECK (mood IN ('funny', 'sweet', 'stressful', 'proud', 'random')),
    text        TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 500),
    song        TEXT CHECK (song IS NULL OR length(song) BETWEEN 1 AND 120),  -- "Title - Artist"
    image_file  TEXT,                           -- server-generated file name, never user-supplied
    visibility  TEXT NOT NULL DEFAULT 'shared' CHECK (visibility IN ('shared', 'private')),
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),  -- UTC
    CHECK (song IS NULL OR image_file IS NULL)
);
CREATE INDEX IF NOT EXISTS idx_moments_couple_time ON moments (couple_id, created_at);

-- Connection Pulse: one weekly check-in per partner. Read ONLY through app/pulses.py, which
-- applies the reveal rule: a partner's score/contact for a week are visible only once BOTH have
-- submitted that week, and a note only if it is also shared (note_shared = 1).
CREATE TABLE IF NOT EXISTS pulses (
    id           INTEGER PRIMARY KEY,
    couple_id    INTEGER NOT NULL REFERENCES couples(id),
    user_id      INTEGER NOT NULL REFERENCES users(id),
    week_start   TEXT NOT NULL,                 -- ISO date of that week's Monday (author's local week)
    score        INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
    contact      TEXT NOT NULL DEFAULT 'right' CHECK (contact IN ('too_little', 'right', 'too_much')),
    note         TEXT CHECK (note IS NULL OR length(note) <= 500),
    note_shared  INTEGER NOT NULL DEFAULT 0 CHECK (note_shared IN (0, 1)),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),  -- UTC; updated when the pulse is edited
    UNIQUE (user_id, week_start)
);

-- This week's nudge from the Drift Detector, cached so both partners see the same one and the
-- model isn't called again until the signals change.
CREATE TABLE IF NOT EXISTS nudges (
    id            INTEGER PRIMARY KEY,
    couple_id     INTEGER NOT NULL REFERENCES couples(id),
    signals_hash  TEXT NOT NULL,                -- hash of the computed signals it was made from
    source        TEXT NOT NULL CHECK (source IN ('llm', 'fallback')),
    suggestion    TEXT NOT NULL,
    explanation   TEXT NOT NULL,
    request_id    TEXT,                         -- llm_calls.request_id, for the token badge
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (couple_id, signals_hash)
);

-- Ritual Builder: shared activities the couple planned or did. Each one comes from the catalog
-- (data/ritual_catalog.json, by catalog_id) with an optional personal variation.
CREATE TABLE IF NOT EXISTS rituals (
    id           INTEGER PRIMARY KEY,
    couple_id    INTEGER NOT NULL REFERENCES couples(id),
    catalog_id   TEXT,                          -- id in data/ritual_catalog.json (NULL for old manual ones)
    title        TEXT NOT NULL,
    variation    TEXT CHECK (variation IS NULL OR length(variation) <= 300),
    category     TEXT NOT NULL CHECK (category IN ('cook', 'watch', 'game', 'creative', 'letters', 'goodnight', 'other')),
    description  TEXT NOT NULL DEFAULT '',
    minutes      INTEGER CHECK (minutes IS NULL OR minutes > 0),
    energy       TEXT CHECK (energy IN ('low', 'medium', 'high')),
    status       TEXT NOT NULL DEFAULT 'planned'
                 CHECK (status IN ('suggested', 'planned', 'done', 'skipped')),
    source       TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'agent', 'seed')),
    done_on      TEXT,                          -- ISO date, when status = 'done'
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- How each partner felt about a ritual they did (1-5 + optional note, visible to both partners).
-- The Ritual Builder feeds the highest- and lowest-rated rituals into its next suggestions.
-- flagged = 1: the note tripped the crisis pre-check, so it is hidden from the partner and never
-- sent to a model.
CREATE TABLE IF NOT EXISTS ritual_feedback (
    id            INTEGER PRIMARY KEY,
    ritual_id     INTEGER NOT NULL REFERENCES rituals(id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    rating        INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    note          TEXT CHECK (note IS NULL OR length(note) <= 200),
    flagged       INTEGER NOT NULL DEFAULT 0 CHECK (flagged IN (0, 1)),
    created_at    TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (ritual_id, user_id)
);

-- Every model call, for the token panel, the per-user budget, and the tokenomics report.
-- input_tokens includes cached and cache-write tokens (that is how the API reports it).
CREATE TABLE IF NOT EXISTS llm_calls (
    id                  INTEGER PRIMARY KEY,
    created_at          TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    user_id             INTEGER REFERENCES users(id),
    agent_name          TEXT NOT NULL,
    request_id          TEXT NOT NULL,          -- groups the calls of one user request
    model               TEXT,                   -- model name the API says served the call
    status              TEXT NOT NULL CHECK (status IN ('ok', 'error')),
    error               TEXT,
    input_tokens        INTEGER NOT NULL DEFAULT 0,
    cached_tokens       INTEGER NOT NULL DEFAULT 0,
    cache_write_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens       INTEGER NOT NULL DEFAULT 0,
    reasoning_tokens    INTEGER NOT NULL DEFAULT 0,  -- already included in output_tokens
    cost_usd            REAL NOT NULL DEFAULT 0,
    latency_ms          INTEGER,
    gateway_cache       TEXT                    -- Portkey response cache: 'HIT', 'MISS', or NULL
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_user_time ON llm_calls (user_id, created_at);

-- One row per chat request handled by the orchestrator: which agent ran, how it was routed, the
-- full step-by-step trace (guard in -> router -> agent -> guard out), and the request's totals.
-- request_id links to llm_calls. Each partner sees only their own runs.
CREATE TABLE IF NOT EXISTS agent_runs (
    id             INTEGER PRIMARY KEY,
    created_at     TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
    user_id        INTEGER NOT NULL REFERENCES users(id),
    message        TEXT NOT NULL,
    status         TEXT NOT NULL CHECK (status IN ('ok', 'blocked', 'support', 'clarify', 'error')),
    intent         TEXT,
    agent          TEXT,
    route_method   TEXT,                        -- rules | llm | clarify | guard
    request_id     TEXT NOT NULL,
    model_calls    INTEGER NOT NULL DEFAULT 0,
    input_tokens   INTEGER NOT NULL DEFAULT 0,
    cached_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens  INTEGER NOT NULL DEFAULT 0,
    cost_usd       REAL NOT NULL DEFAULT 0,
    trace          TEXT NOT NULL                -- JSON list of {stage, detail}
);
CREATE INDEX IF NOT EXISTS idx_agent_runs_user ON agent_runs (user_id, id);

-- Bump when the schema changes; app/db.py refuses to open an older database.
PRAGMA user_version = 7;

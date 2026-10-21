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
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS users (
    id                  INTEGER PRIMARY KEY,
    couple_id           INTEGER NOT NULL REFERENCES couples(id),
    role                TEXT NOT NULL CHECK (role IN ('A', 'B')),  -- Partner A / Partner B
    name                TEXT NOT NULL,
    city                TEXT,
    timezone            TEXT,                   -- IANA name, e.g. "America/New_York"
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

-- Connection Pulse: one weekly check-in per partner.
-- note_shared = 0 means the note is private to its author (the drift agent may use the score,
-- but never quotes or reveals a private note to the other partner).
CREATE TABLE IF NOT EXISTS pulses (
    id           INTEGER PRIMARY KEY,
    couple_id    INTEGER NOT NULL REFERENCES couples(id),
    user_id      INTEGER NOT NULL REFERENCES users(id),
    week_start   TEXT NOT NULL,                 -- ISO date of that week's Monday
    score        INTEGER NOT NULL CHECK (score BETWEEN 1 AND 5),
    note         TEXT CHECK (note IS NULL OR length(note) <= 500),
    note_shared  INTEGER NOT NULL DEFAULT 0 CHECK (note_shared IN (0, 1)),
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (user_id, week_start)
);

-- Ritual Builder: shared activities, suggested by the agent or added by hand.
CREATE TABLE IF NOT EXISTS rituals (
    id           INTEGER PRIMARY KEY,
    couple_id    INTEGER NOT NULL REFERENCES couples(id),
    title        TEXT NOT NULL,
    category     TEXT NOT NULL CHECK (category IN ('cook', 'watch', 'game', 'letter', 'other')),
    description  TEXT NOT NULL DEFAULT '',
    minutes      INTEGER CHECK (minutes IS NULL OR minutes > 0),
    energy       TEXT CHECK (energy IN ('low', 'medium', 'high')),
    status       TEXT NOT NULL DEFAULT 'suggested'
                 CHECK (status IN ('suggested', 'planned', 'done', 'skipped')),
    source       TEXT NOT NULL DEFAULT 'manual' CHECK (source IN ('manual', 'agent', 'seed')),
    done_on      TEXT,                          -- ISO date, when status = 'done'
    created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- How each partner felt about a ritual they tried. The Ritual Builder learns from this.
CREATE TABLE IF NOT EXISTS ritual_feedback (
    id            INTEGER PRIMARY KEY,
    ritual_id     INTEGER NOT NULL REFERENCES rituals(id) ON DELETE CASCADE,
    user_id       INTEGER NOT NULL REFERENCES users(id),
    rating        INTEGER NOT NULL CHECK (rating BETWEEN 1 AND 5),
    would_repeat  INTEGER NOT NULL DEFAULT 1 CHECK (would_repeat IN (0, 1)),
    comment       TEXT CHECK (comment IS NULL OR length(comment) <= 500),
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
    latency_ms          INTEGER
);
CREATE INDEX IF NOT EXISTS idx_llm_calls_user_time ON llm_calls (user_id, created_at);

-- Bump when the schema changes; app/db.py refuses to open an older database.
PRAGMA user_version = 2;

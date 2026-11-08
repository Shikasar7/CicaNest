-- Model endpoints. key_ref names an .env entry; options holds kind-specific settings (JSON),
-- e.g. {"proxy": "...", "auth_file": "...", "temperature": 0.8}.
CREATE TABLE providers (
    id         TEXT PRIMARY KEY,                     -- e.g. "deepseek"
    kind       TEXT NOT NULL,                        -- "openai_chat" | "openai_responses" | "codex"
    base_url   TEXT NOT NULL,
    model      TEXT NOT NULL,
    key_ref    TEXT NOT NULL DEFAULT '',
    options    TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE personas (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    content    TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);

-- One bot per account. mode: "observe" (decide, never send) | "live" (send; M3) | "paused".
CREATE TABLE bots (
    id          TEXT PRIMARY KEY,                    -- account id, e.g. "qq:900000001"
    account_id  TEXT NOT NULL UNIQUE REFERENCES accounts (id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    aliases     TEXT NOT NULL DEFAULT '[]',          -- JSON array of names that count as calling the bot
    provider_id TEXT REFERENCES providers (id) ON DELETE SET NULL,
    mode        TEXT NOT NULL DEFAULT 'observe',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL
);

-- Where a bot participates. activity: "mentions" | "occasional" | "active".
CREATE TABLE bot_channels (
    bot_id           TEXT NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
    channel_id       INTEGER NOT NULL REFERENCES channels (id) ON DELETE CASCADE,
    enabled          INTEGER NOT NULL DEFAULT 1,
    activity         TEXT NOT NULL DEFAULT 'occasional',
    persona_id       INTEGER REFERENCES personas (id) ON DELETE SET NULL,
    cooldown_seconds REAL NOT NULL DEFAULT 12,
    PRIMARY KEY (bot_id, channel_id)
);

CREATE TABLE llm_calls (
    id                INTEGER PRIMARY KEY,
    bot_id            TEXT,
    provider_id       TEXT NOT NULL,
    model             TEXT NOT NULL,
    started_at        REAL NOT NULL,
    latency_ms        REAL,
    status            TEXT NOT NULL,                 -- "ok" | "error"
    error             TEXT,
    prompt_chars      INTEGER NOT NULL DEFAULT 0,
    prompt_tokens     INTEGER,
    completion_tokens INTEGER,
    output            TEXT                           -- raw model text, truncated
);
CREATE INDEX llm_calls_started ON llm_calls (started_at);

-- One row per evaluation (or suppressed trigger) of a bot in a channel.
-- outcome: "would_reply" | "replied" | "skipped" | "suppressed" | "failed"
CREATE TABLE decisions (
    id             INTEGER PRIMARY KEY,
    bot_id         TEXT NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
    channel_id     INTEGER NOT NULL REFERENCES channels (id) ON DELETE CASCADE,
    trigger_id     INTEGER REFERENCES messages (id) ON DELETE SET NULL,
    trigger        TEXT NOT NULL,                    -- "mention" | "chime_in"
    outcome        TEXT NOT NULL,
    reason         TEXT NOT NULL DEFAULT '',
    reply          TEXT,
    quote          INTEGER NOT NULL DEFAULT 0,       -- reply would quote the target message
    regenerated    INTEGER NOT NULL DEFAULT 0,
    llm_call_id    INTEGER REFERENCES llm_calls (id) ON DELETE SET NULL,
    created_at     REAL NOT NULL
);
CREATE INDEX decisions_channel_created ON decisions (channel_id, created_at);
CREATE INDEX decisions_bot_created ON decisions (bot_id, created_at);

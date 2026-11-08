-- Platform accounts we operate (e.g. each QQ bot number). Secrets are not stored here:
-- *_token_ref names an entry in .env that holds the actual token.
CREATE TABLE accounts (
    id             TEXT PRIMARY KEY,                 -- "qq:900000001"
    platform       TEXT NOT NULL,
    self_id        TEXT NOT NULL,
    name           TEXT NOT NULL DEFAULT '',
    http_url       TEXT NOT NULL,
    ws_url         TEXT NOT NULL,
    http_token_ref TEXT NOT NULL DEFAULT '',
    ws_token_ref   TEXT NOT NULL DEFAULT '',
    enabled        INTEGER NOT NULL DEFAULT 1,
    created_at     REAL NOT NULL,
    updated_at     REAL NOT NULL,
    UNIQUE (platform, self_id)
);

-- A group or private chat as seen from one account. The same QQ group joined by three bots is
-- three channels: message ids are per-account on QQ, so views are not merged at this level.
CREATE TABLE channels (
    id              INTEGER PRIMARY KEY,
    account_id      TEXT NOT NULL REFERENCES accounts (id) ON DELETE CASCADE,
    kind            TEXT NOT NULL,                   -- "group" | "private"
    external_id     TEXT NOT NULL,                   -- group number / peer id
    name            TEXT NOT NULL DEFAULT '',
    last_message_at REAL,
    UNIQUE (account_id, kind, external_id)
);

CREATE TABLE messages (
    id          INTEGER PRIMARY KEY,
    channel_id  INTEGER NOT NULL REFERENCES channels (id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,                       -- platform message id
    sender_id   TEXT NOT NULL,
    sender_name TEXT NOT NULL,
    from_self   INTEGER NOT NULL,
    text        TEXT NOT NULL,
    segments    TEXT NOT NULL,                       -- JSON array of {type, data}
    mentions    TEXT NOT NULL DEFAULT '[]',          -- JSON array of mentioned ids
    reply_to    TEXT,
    sent_at     REAL NOT NULL,                       -- platform timestamp
    received_at REAL NOT NULL,                       -- when nest stored it
    source      TEXT NOT NULL,                       -- "live" | "backfill"
    recalled_at REAL,
    UNIQUE (channel_id, external_id)
);

CREATE INDEX messages_channel_sent ON messages (channel_id, sent_at);
CREATE INDEX messages_received ON messages (received_at);

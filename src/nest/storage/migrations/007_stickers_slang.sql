-- 表情包: stickers seen in the groups that collect them. One row per picture,
-- shared by every group and bot; the file is data/stickers/<sha256[:2]>/<sha256>.<ext>.
CREATE TABLE stickers (
    id            INTEGER PRIMARY KEY,
    sha256        TEXT NOT NULL UNIQUE,
    source_key    TEXT NOT NULL,                   -- "md5:<QQ's file MD5>" | "mface:<emoji id>": dedupe before download
    kind          TEXT NOT NULL,                   -- "image" (a collected 动画表情) | "mface" (表情商城)
    content_type  TEXT NOT NULL,                   -- "image/png" | "image/jpeg" | "image/gif" | "image/webp"
    bytes         INTEGER NOT NULL,
    width         INTEGER NOT NULL,
    height        INTEGER NOT NULL,
    animated      INTEGER NOT NULL DEFAULT 0,
    group_key     TEXT NOT NULL,                   -- where it was first seen, e.g. "qq:group:800000001"
    seen_count    INTEGER NOT NULL DEFAULT 1,
    first_seen_at REAL NOT NULL,
    last_seen_at  REAL NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending', -- "pending" | "ready" | "unsuitable" | "failed"
    status_at     REAL NOT NULL,                   -- when status last changed ("unsuitable" expires after a week)
    note          TEXT NOT NULL DEFAULT '',        -- why it is unsuitable or failed
    description   TEXT NOT NULL DEFAULT '',
    caption       TEXT NOT NULL DEFAULT '',        -- the words in the picture
    tags          TEXT NOT NULL DEFAULT '[]',      -- JSON array, at most 3
    edited        INTEGER NOT NULL DEFAULT 0,      -- description changed by hand: never re-described automatically
    kept          INTEGER NOT NULL DEFAULT 0,      -- 「保留」: never removed to make room
    used_count    INTEGER NOT NULL DEFAULT 0,
    last_used_at  REAL
);
CREATE INDEX stickers_source ON stickers (source_key);
CREATE INDEX stickers_status ON stickers (status, last_seen_at);

-- What each group lets nest do with it, by real group (the same QQ group seen by several accounts is one row).
CREATE TABLE group_features (
    group_key           TEXT PRIMARY KEY,         -- "qq:group:<number>"
    collect_stickers    INTEGER NOT NULL DEFAULT 0,
    learn_slang         INTEGER NOT NULL DEFAULT 0,
    slang_learned_at    REAL,                     -- last learning run
    slang_learned_until REAL                      -- sent_at of the newest message it read
);

-- 黑话: what words mean in one group.
CREATE TABLE slang (
    id            INTEGER PRIMARY KEY,
    group_key     TEXT NOT NULL,
    term          TEXT NOT NULL,
    meaning       TEXT NOT NULL,
    example       TEXT NOT NULL DEFAULT '',
    source        TEXT NOT NULL DEFAULT 'auto',    -- "auto" (learned) | "edited" (changed or confirmed by hand) | "manual"
    hits          INTEGER NOT NULL DEFAULT 0,      -- times seen in the messages learning read
    first_seen_at REAL NOT NULL,
    last_seen_at  REAL NOT NULL,
    updated_at    REAL NOT NULL,
    UNIQUE (group_key, term)
);

-- One row per learning run of a group.
CREATE TABLE slang_runs (
    id          INTEGER PRIMARY KEY,
    group_key   TEXT NOT NULL,
    started_at  REAL NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    messages    INTEGER NOT NULL DEFAULT 0,
    added       INTEGER NOT NULL DEFAULT 0,
    updated     INTEGER NOT NULL DEFAULT 0,
    dropped     INTEGER NOT NULL DEFAULT 0,
    detail      TEXT NOT NULL DEFAULT '',          -- what happened, in Chinese
    error       TEXT
);
CREATE INDEX slang_runs_group_started ON slang_runs (group_key, started_at);

-- Per bot per group: send stickers ("off" | "trial": recorded, never sent | "on") and use the group's slang.
ALTER TABLE bot_channels ADD COLUMN sticker_mode TEXT NOT NULL DEFAULT 'off';
ALTER TABLE bot_channels ADD COLUMN use_slang INTEGER NOT NULL DEFAULT 0;

-- The sticker a reply carried. sticker_state: "sent" | "trial" (would have sent) | "failed" | "unknown"
-- | NULL (no sticker). slang_terms: JSON array of the terms explained in the prompt, NULL for none.
ALTER TABLE decisions ADD COLUMN sticker_id INTEGER REFERENCES stickers (id) ON DELETE SET NULL;
ALTER TABLE decisions ADD COLUMN sticker_state TEXT;
ALTER TABLE decisions ADD COLUMN slang_terms TEXT;

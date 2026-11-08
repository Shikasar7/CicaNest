-- 监控: one source (feed, page or price) checked on a schedule. When something new or changed shows up,
-- one of the bots posts a short message to one QQ group or private chat (mode "push"), or the message is
-- only recorded (mode "preview", the default).
CREATE TABLE watches (
    id               INTEGER PRIMARY KEY,
    name             TEXT NOT NULL,
    kind             TEXT NOT NULL,                   -- "feed" | "page" | "price"
    url              TEXT NOT NULL,
    selector         TEXT NOT NULL DEFAULT '',        -- CSS selector; '' = the whole <body>
    threshold        REAL,                            -- price only: push only at or below this
    interval_minutes INTEGER NOT NULL,
    bot_id           TEXT NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
    target_kind      TEXT NOT NULL,                   -- "group" | "private"
    target_id        TEXT NOT NULL,                   -- group number / QQ number
    provider_id      TEXT REFERENCES providers (id) ON DELETE SET NULL,  -- NULL: the plain template
    instructions     TEXT NOT NULL DEFAULT '',        -- extra wishes for the model
    use_proxy        INTEGER NOT NULL DEFAULT 0,      -- fetch through the system setting fetch_proxy
    mode             TEXT NOT NULL DEFAULT 'preview', -- "preview" | "push"
    enabled          INTEGER NOT NULL DEFAULT 1,
    state            TEXT NOT NULL DEFAULT '{}',      -- JSON: seen entry ids / last text / last price
    last_run_at      REAL,
    next_run_at      REAL,
    created_at       REAL NOT NULL,
    updated_at       REAL NOT NULL
);
CREATE INDEX watches_due ON watches (enabled, next_run_at);

-- One row per check. outcome: "baseline" | "unchanged" | "changed" | "error".
-- delivered: "sent" | "preview" | "paused" | "failed" | "unknown" | NULL (nothing to push, or held back).
CREATE TABLE watch_runs (
    id          INTEGER PRIMARY KEY,
    watch_id    INTEGER NOT NULL REFERENCES watches (id) ON DELETE CASCADE,
    started_at  REAL NOT NULL,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    outcome     TEXT NOT NULL,
    changes     INTEGER NOT NULL DEFAULT 0,
    detail      TEXT NOT NULL DEFAULT '',             -- what happened, in Chinese
    message     TEXT,                                 -- what was pushed, or would have been
    delivered   TEXT,
    error       TEXT
);
CREATE INDEX watch_runs_watch_started ON watch_runs (watch_id, started_at);
CREATE INDEX watch_runs_started ON watch_runs (started_at);

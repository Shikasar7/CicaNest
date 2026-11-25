-- 提醒 (伙伴模式): what the user asked a companion to remind them of, in their chat with her (channel_id).
-- due_at: the next time it goes off (unix seconds). repeat: '' once, 'daily', 'weekdays', 'weekly',
-- 'monthly'. also_qq: also sent to the user's QQ private chat even when the desktop app is open (it always
-- goes there when the app is not). active 0: done (a one-off that went off) or switched off. nudge_at: when
-- to remind once more if the user has said nothing since it went off; fired_at: when it last went off.
CREATE TABLE reminders (
    id         INTEGER PRIMARY KEY,
    bot_id     TEXT NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
    channel_id INTEGER NOT NULL REFERENCES channels (id) ON DELETE CASCADE,
    text       TEXT NOT NULL,
    due_at     REAL NOT NULL,
    repeat     TEXT NOT NULL DEFAULT '',
    also_qq    INTEGER NOT NULL DEFAULT 0,
    active     INTEGER NOT NULL DEFAULT 1,
    source     TEXT NOT NULL DEFAULT 'console',
    fired_at   REAL,
    nudge_at   REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX reminders_due ON reminders (active, due_at);
CREATE INDEX reminders_bot ON reminders (bot_id);

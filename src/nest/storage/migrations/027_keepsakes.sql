-- 回忆档案 (伙伴模式): a companion's own records of the time with the user, kept word for word — what a
-- companion brought from elsewhere (a backup from another app), or what was added since. Never all in the prompt:
-- the parts that match what is being talked about are looked up (nest.core.keepsakes). Only used in the
-- bot's one-to-one chat with the user. source: the file it came from ("" when typed in the console).
CREATE TABLE keepsakes (
    id         INTEGER PRIMARY KEY,
    bot_id     TEXT NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX keepsakes_bot ON keepsakes (bot_id, id);

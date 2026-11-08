-- How each bot looks in the console: at most one picture per source ("qq": its QQ avatar, fetched on
-- request; "custom": one the user uploaded). The chosen picture replaces the bot's brush character; with
-- none chosen the character is shown.
CREATE TABLE bot_avatars (
    bot_id       TEXT NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
    source       TEXT NOT NULL,                   -- "qq" | "custom"
    content_type TEXT NOT NULL,                   -- "image/png" | "image/jpeg" | "image/webp" | "image/gif"
    data         BLOB NOT NULL,
    chosen       INTEGER NOT NULL DEFAULT 0,      -- at most one per bot
    updated_at   REAL NOT NULL,
    PRIMARY KEY (bot_id, source)
);

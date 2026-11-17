-- 长期记忆: what the bots remember about people, learned in the background from the chat and used only
-- where it was learned. Set per bot in each group or private chat (the bot's settings there), off by
-- default: a place is learned from while any bot there has it on, and only those bots are told.
ALTER TABLE bot_channels ADD COLUMN remember INTEGER NOT NULL DEFAULT 0;
-- place: a group ("qq:group:123", shared by every account of ours in it) or one private chat
-- ("chat:<channels.id>"). kind: "like" (喜好), "fact" (情况), "promise" (约定). bot_id: for a 约定 the bot
-- it was made with, which alone is told; NULL for the rest, which every bot there is told. due: the day a
-- 约定 is for (YYYY-MM-DD), when it has one. source "manual": added or edited in the console, so learning
-- never changes or removes it. used_at: last time it went into a prompt.
CREATE TABLE memories (
    id         INTEGER PRIMARY KEY,
    place      TEXT NOT NULL,
    person_id  TEXT NOT NULL,
    person     TEXT NOT NULL,
    bot_id     TEXT,
    kind       TEXT NOT NULL,
    content    TEXT NOT NULL,
    due        TEXT,
    source     TEXT NOT NULL DEFAULT 'learned',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    used_at    REAL
);
CREATE INDEX memories_place ON memories (place, person_id);
-- How far each place has been read (messages' sent_at), and the last run.
CREATE TABLE memory_progress (
    place      TEXT PRIMARY KEY,
    upto       REAL NOT NULL DEFAULT 0,
    learned_at REAL NOT NULL DEFAULT 0,
    detail     TEXT NOT NULL DEFAULT '',
    error      TEXT NOT NULL DEFAULT ''
);

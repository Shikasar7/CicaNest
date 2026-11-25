-- 小窝和她的日子: each companion's life profile, the house's schedule for each day, and the
-- small things of her day she has told the user (so she does not contradict herself later).
CREATE TABLE life_profiles (
    bot_id     TEXT PRIMARY KEY REFERENCES bots (id) ON DELETE CASCADE,
    profile    TEXT NOT NULL,          -- JSON: wake, sleep, rooms, hobbies, lately, habits, friends, home
    updated_at REAL NOT NULL
);

CREATE TABLE life_days (
    day        TEXT PRIMARY KEY,       -- "2026-11-24", local time
    plan       TEXT NOT NULL,          -- JSON: bot id -> [{from, to, room, doing, mood, share}]
    made_by    TEXT NOT NULL,          -- the bot whose model wrote it, or "default"
    created_at REAL NOT NULL
);

CREATE TABLE life_moments (
    id     INTEGER PRIMARY KEY,
    bot_id TEXT NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
    at     REAL NOT NULL,
    text   TEXT NOT NULL,
    source TEXT NOT NULL               -- "share" (she told the user), "chat", "group"
);
CREATE INDEX life_moments_bot ON life_moments (bot_id, at);

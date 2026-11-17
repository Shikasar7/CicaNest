-- 生日祝福: set per bot in each group (the bot's group settings), off by default. On a group member's
-- birthday one of the bots that have it on there wishes them a happy birthday.
ALTER TABLE bot_channels ADD COLUMN birthday INTEGER NOT NULL DEFAULT 0;
-- Birthdays as people's profiles show them, looked up again after a while. month = day = 0: not shown
-- (not set, or kept private).
CREATE TABLE birthdays (
    platform   TEXT NOT NULL,
    user_id    TEXT NOT NULL,
    month      INTEGER NOT NULL DEFAULT 0,
    day        INTEGER NOT NULL DEFAULT 0,
    checked_at REAL NOT NULL,
    recheck_at REAL NOT NULL,
    PRIMARY KEY (platform, user_id)
);
-- Who was wished in which group, once a year. outcome: "replied" (sent, or maybe sent), "would_reply"
-- (an observing bot only noted it), "failed" (tried `tries` times, given up after a few).
CREATE TABLE birthday_wishes (
    group_key TEXT NOT NULL,
    user_id   TEXT NOT NULL,
    year      INTEGER NOT NULL,
    bot_id    TEXT NOT NULL,
    outcome   TEXT NOT NULL,
    said      TEXT NOT NULL DEFAULT '',
    tries     INTEGER NOT NULL DEFAULT 1,
    at        REAL NOT NULL,
    PRIMARY KEY (group_key, user_id, year)
);
CREATE INDEX birthday_wishes_bot ON birthday_wishes (bot_id, at);

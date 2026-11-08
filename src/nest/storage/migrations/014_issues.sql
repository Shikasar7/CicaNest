-- 问题记录 (nest.core.issues): what the AI bots could not solve in a group, and feedback about them,
-- written down by the group's 记录官 (one bot per group) and sent to the user once or more a day.
ALTER TABLE bot_channels ADD COLUMN recorder INTEGER NOT NULL DEFAULT 0;

CREATE TABLE issues (
    id          INTEGER PRIMARY KEY,
    group_id    TEXT NOT NULL,                  -- the QQ group number
    group_name  TEXT NOT NULL DEFAULT '',
    bot_id      TEXT NOT NULL,                  -- the 记录官 that wrote it down
    message_id  INTEGER,                        -- messages.id it starts from (gone after the history purge)
    said_at     REAL NOT NULL,                  -- when that message was sent
    speaker     TEXT NOT NULL DEFAULT '',
    speaker_id  TEXT NOT NULL DEFAULT '',
    kind        TEXT NOT NULL,                  -- "unsolved" | "wrong" | "bug" | "request" | "feedback"
    summary     TEXT NOT NULL,
    quote       TEXT NOT NULL DEFAULT '',       -- what was said, as said (kept after the purge)
    ai_reply    TEXT NOT NULL DEFAULT '',       -- what the bots answered, in short
    status      TEXT NOT NULL DEFAULT 'open',   -- "open" | "done" | "ignored"
    created_at  REAL NOT NULL,
    reported_at REAL,                           -- sent in a daily digest
    UNIQUE (group_id, message_id)
);
CREATE INDEX issues_said ON issues (said_at);

-- How far each 记录官 has read its group: messages after this one are still to be looked at.
CREATE TABLE issue_reviews (
    channel_id      INTEGER PRIMARY KEY REFERENCES channels (id) ON DELETE CASCADE,
    last_message_id INTEGER NOT NULL,
    reviewed_at     REAL NOT NULL
);

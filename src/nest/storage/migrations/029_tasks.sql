-- 任务 (伙伴模式): something a companion works on in the background, round after round with her
-- tools, and reports on in her chat with the user (channel_id) when done.
-- status: 'queued', 'running', 'done', 'failed', 'stopped' (by the user), 'limit' (a limit ran out: she
-- reported how far she got), 'interrupted' (nest restarted while it ran; not re-run by itself, the user may
-- resume it). source: 'chat' (she started it while talking), 'console'. steps / tokens / seconds: used so
-- far, over every run of it; max_*: its limits. report: her last words on it (also sent to the chat).
CREATE TABLE tasks (
    id          INTEGER PRIMARY KEY,
    bot_id      TEXT NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
    channel_id  INTEGER NOT NULL REFERENCES channels (id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    goal        TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'queued',
    source      TEXT NOT NULL DEFAULT 'chat',
    provider_id TEXT NOT NULL DEFAULT '',
    steps       INTEGER NOT NULL DEFAULT 0,
    tokens      INTEGER NOT NULL DEFAULT 0,
    seconds     REAL NOT NULL DEFAULT 0,
    max_steps   INTEGER NOT NULL,
    max_seconds REAL NOT NULL,
    max_tokens  INTEGER NOT NULL,
    report      TEXT NOT NULL DEFAULT '',
    error       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    started_at  REAL,
    ended_at    REAL,
    updated_at  REAL NOT NULL
);
CREATE INDEX tasks_bot ON tasks (bot_id, id);
CREATE INDEX tasks_status ON tasks (status);

-- What happened in a task, in order. kind: 'say' (her words between tools), 'tool' (a call: tool, given as
-- JSON, result cut to a few thousand characters; status 'running' until it returns), 'note' (the user added
-- something while it ran: seen 0 until she has been told), 'stop' (why it ended early). ms: how long a tool
-- took.
CREATE TABLE task_steps (
    id         INTEGER PRIMARY KEY,
    task_id    INTEGER NOT NULL REFERENCES tasks (id) ON DELETE CASCADE,
    kind       TEXT NOT NULL,
    tool       TEXT NOT NULL DEFAULT '',
    given      TEXT NOT NULL DEFAULT '',
    result     TEXT NOT NULL DEFAULT '',
    status     TEXT NOT NULL DEFAULT 'done',
    seen       INTEGER NOT NULL DEFAULT 1,
    ms         REAL,
    created_at REAL NOT NULL
);
CREATE INDEX task_steps_task ON task_steps (task_id, id);

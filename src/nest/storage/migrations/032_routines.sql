-- 定时任务 (伙伴模式): something a companion does again and again at set times —
-- each time a task (029) is started for it, in her chat with the user (channel_id).
-- first_at: the first time, which also fixes the time of day / weekday / day of the month; repeat: 'daily',
-- 'weekdays', 'weekly', 'monthly' (as reminders); next_at: when it runs next. also_qq: the report also goes
-- to QQ (else, like reminders, the desk when the desktop app is open, QQ when not). source: 'chat' (she set
-- it when asked), 'console'.
CREATE TABLE routines (
    id         INTEGER PRIMARY KEY,
    bot_id     TEXT NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
    channel_id INTEGER NOT NULL REFERENCES channels (id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    goal       TEXT NOT NULL,
    first_at   REAL NOT NULL,
    repeat     TEXT NOT NULL,
    next_at    REAL NOT NULL,
    enabled    INTEGER NOT NULL DEFAULT 1,
    also_qq    INTEGER NOT NULL DEFAULT 0,
    source     TEXT NOT NULL DEFAULT 'console',
    last_at    REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX routines_bot ON routines (bot_id, id);
CREATE INDEX routines_next ON routines (enabled, next_at);

-- Each time one was due: the task started for it, or why none was (skipped: the last one still running,
-- nest was not running then, the task could not start).
CREATE TABLE routine_runs (
    id         INTEGER PRIMARY KEY,
    routine_id INTEGER NOT NULL REFERENCES routines (id) ON DELETE CASCADE,
    at         REAL NOT NULL,
    task_id    INTEGER REFERENCES tasks (id) ON DELETE SET NULL,
    skipped    TEXT NOT NULL DEFAULT ''
);
CREATE INDEX routine_runs_routine ON routine_runs (routine_id, id);

-- A task started at a set time runs with nobody there to nod: steps that need the user's nod are not done
-- (she says so in her report). also_qq: where its report goes, as the routine says.
ALTER TABLE tasks ADD COLUMN unattended INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN also_qq INTEGER NOT NULL DEFAULT 0;
ALTER TABLE tasks ADD COLUMN routine_id INTEGER REFERENCES routines (id) ON DELETE SET NULL;

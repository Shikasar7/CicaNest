-- 确认 (伙伴模式): a step a companion wanted to take that needs the
-- user's nod — what exactly (plugin, tool, everything it was given), in which chat or task, and what came
-- of it. status: 'pending', 'allowed' (once), 'allowed_kind' (and the same tool again in this answer or
-- task), 'denied', 'expired' (nobody answered in time, or nest restarted). decided_by: the device that
-- answered (address and browser), never a QQ message.
CREATE TABLE approvals (
    id          INTEGER PRIMARY KEY,
    bot_id      TEXT NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
    channel_id  INTEGER REFERENCES channels (id) ON DELETE SET NULL,
    task_id     INTEGER REFERENCES tasks (id) ON DELETE SET NULL,
    plugin_id   TEXT NOT NULL DEFAULT '',
    plugin_name TEXT NOT NULL DEFAULT '',
    tool        TEXT NOT NULL,
    given       TEXT NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL DEFAULT 'pending',
    decided_by  TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    decided_at  REAL
);
CREATE INDEX approvals_status ON approvals (status, id);
CREATE INDEX approvals_bot ON approvals (bot_id, id);

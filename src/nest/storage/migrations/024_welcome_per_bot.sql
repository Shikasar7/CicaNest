-- 欢迎新人 is set per bot in each group (the bot's group settings) instead of per group: which bots
-- welcome (one of them at random), spoken when it can or always as text, after about how many seconds,
-- and a line sent after the welcome (say, to read the group notice).
ALTER TABLE bot_channels ADD COLUMN welcome INTEGER NOT NULL DEFAULT 0;
ALTER TABLE bot_channels ADD COLUMN welcome_voice INTEGER NOT NULL DEFAULT 1;
ALTER TABLE bot_channels ADD COLUMN welcome_delay INTEGER NOT NULL DEFAULT 5;
ALTER TABLE bot_channels ADD COLUMN welcome_note TEXT NOT NULL DEFAULT '';
-- A group that had it on keeps it: every bot switched on there may be the one that welcomes, as before.
UPDATE bot_channels SET welcome = 1
WHERE enabled = 1 AND channel_id IN (
    SELECT c.id FROM channels c
    JOIN accounts a ON a.id = c.account_id
    JOIN group_features g ON g.group_key = a.platform || ':group:' || c.external_id
    WHERE c.kind = 'group' AND g.welcome = 1
);
ALTER TABLE group_features DROP COLUMN welcome;

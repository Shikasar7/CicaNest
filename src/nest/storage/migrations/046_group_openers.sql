-- 冷场时主动开口 (nest.core.openers): in a group of a platform that has been quiet for `opener_quiet` minutes,
-- this bot may say something on her own (small talk, a grumble, being cute, asking who wants to play a little
-- game), at most `opener_daily` times a day, in the daytime only. Off by default.
ALTER TABLE bot_channels ADD COLUMN opener INTEGER NOT NULL DEFAULT 0;
ALTER TABLE bot_channels ADD COLUMN opener_quiet INTEGER NOT NULL DEFAULT 120;
ALTER TABLE bot_channels ADD COLUMN opener_daily INTEGER NOT NULL DEFAULT 3;

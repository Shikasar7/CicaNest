-- 回忆草稿 (伙伴模式): before her chat with the user is cleaned away (history_days), she writes what was worth
-- keeping into a record of her 回忆档案 — a draft, which she does not look things up in until the user has
-- read it and kept it (nest.core.keepsake_drafts). keepsake_progress: per bot, how far her chat is written up.
ALTER TABLE keepsakes ADD COLUMN draft INTEGER NOT NULL DEFAULT 0;

CREATE TABLE keepsake_progress (
    bot_id TEXT PRIMARY KEY REFERENCES bots (id) ON DELETE CASCADE,
    upto   REAL NOT NULL,  -- the last message written up (sent_at)
    run_at REAL NOT NULL   -- the last time she wrote one (or decided there was nothing to keep)
);

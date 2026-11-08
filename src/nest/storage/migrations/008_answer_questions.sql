-- 答题: when someone calls the bot with a real question, it answers properly and may search the web first.
ALTER TABLE bot_channels ADD COLUMN answer_questions INTEGER NOT NULL DEFAULT 0;

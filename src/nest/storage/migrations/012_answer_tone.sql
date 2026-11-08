-- 答题的语气 (nest.core.tones): how the bot sounds when it answers a question in this group or private chat.
ALTER TABLE bot_channels ADD COLUMN answer_tone TEXT NOT NULL DEFAULT 'crisp';

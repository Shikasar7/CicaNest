-- 语气 (nest.core.tones): the tones bots speak in, built-in ones seeded at startup (ToneRepo.seed), and
-- the one each bot uses for everyday chat in a group, next to answer_tone (012) for 答题.
CREATE TABLE tones (
    key        TEXT PRIMARY KEY,                -- "crisp", "tsundere"…, or "custom-<n>" for added ones
    label      TEXT NOT NULL UNIQUE,            -- 「傲娇可爱」
    manner     TEXT NOT NULL,                   -- how to talk, added to the prompt
    builtin    INTEGER NOT NULL DEFAULT 0,      -- 1: shipped with nest (can be rewritten and reset, not deleted)
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
ALTER TABLE bot_channels ADD COLUMN chat_tone TEXT NOT NULL DEFAULT 'persona';

-- A bot removed in the console: hidden and silent, its account disabled, its history kept. Adding the same
-- QQ number again brings it back.
ALTER TABLE bots ADD COLUMN deleted_at REAL;

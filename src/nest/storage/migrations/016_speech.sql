-- 发语音: a bot's voice ("<service>:<voice id>", '' for none), the group switch, and what a reply's
-- voice message did: "sent" | "trial" (would have sent) | "failed" | "unknown". NULL: no voice was involved.
ALTER TABLE bots ADD COLUMN voice TEXT NOT NULL DEFAULT '';
ALTER TABLE bot_channels ADD COLUMN voice_mode TEXT NOT NULL DEFAULT 'off';
ALTER TABLE decisions ADD COLUMN voice_state TEXT;

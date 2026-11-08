-- 备用模型: speaks when the bot's own model is out of quota or down.
ALTER TABLE bots ADD COLUMN backup_provider_id TEXT REFERENCES providers (id) ON DELETE SET NULL;
-- 说话示例: a few lines showing how this persona talks, kept apart from who it is.
ALTER TABLE personas ADD COLUMN examples TEXT NOT NULL DEFAULT '';

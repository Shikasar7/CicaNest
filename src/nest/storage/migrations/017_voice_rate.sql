-- 语音频率: besides the replies the model marks, this share (percent) of the others is spoken too.
ALTER TABLE bot_channels ADD COLUMN voice_rate INTEGER NOT NULL DEFAULT 0;

-- 一次最多发: how many messages one reply may come as, a sticker included.
ALTER TABLE bot_channels ADD COLUMN reply_lines INTEGER NOT NULL DEFAULT 3;
-- 分条频率: the share (percent) of replies that may come as several messages; the others are one sentence.
ALTER TABLE bot_channels ADD COLUMN split_rate INTEGER NOT NULL DEFAULT 100;

-- 表情包 added by hand or drawn: a drawn sticker can belong to one bot, and only that bot sends it.
-- NULL: any bot may send it (everything collected from the groups).
ALTER TABLE stickers ADD COLUMN bot_id TEXT;

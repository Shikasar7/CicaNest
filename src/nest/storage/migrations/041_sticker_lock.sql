-- 锁住几格: 「全部重画」 leaves a locked picture as it is.
ALTER TABLE sticker_slots ADD COLUMN locked INTEGER NOT NULL DEFAULT 0;

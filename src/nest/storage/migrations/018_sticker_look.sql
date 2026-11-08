-- 表情包: what a picture looks like (core/stickers.py look_of), to know a re-saved copy of one we have.
ALTER TABLE stickers ADD COLUMN look TEXT NOT NULL DEFAULT '';

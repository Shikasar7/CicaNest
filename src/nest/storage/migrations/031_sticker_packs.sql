-- 表情包套装: each companion's own stickers, one picture per mood ("卖萌", "害羞"…), drawn by
-- nest from her picture or uploaded. A companion may have several packs; the one in use is what she sends
-- in the desk chat. The pictures themselves are rows of `stickers` (owned by her, kind "drawn" / "upload").
CREATE TABLE sticker_packs (
    id         INTEGER PRIMARY KEY,
    bot_id     TEXT NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    style      TEXT NOT NULL DEFAULT 'half',  -- nest.core.sticker_packs.STYLES
    extra      TEXT NOT NULL DEFAULT '',      -- what the user asks of every picture ("都戴着猫耳")
    active     INTEGER NOT NULL DEFAULT 0,    -- the one in use: at most one per bot
    created_at REAL NOT NULL
);
CREATE INDEX sticker_packs_bot ON sticker_packs (bot_id);

CREATE TABLE sticker_slots (
    id         INTEGER PRIMARY KEY,
    pack_id    INTEGER NOT NULL REFERENCES sticker_packs (id) ON DELETE CASCADE,
    position   INTEGER NOT NULL,
    mood       TEXT NOT NULL,
    sticker_id INTEGER REFERENCES stickers (id) ON DELETE SET NULL,
    state      TEXT NOT NULL DEFAULT 'empty', -- "empty" | "queued" | "drawing" | "done" | "failed"
    error      TEXT NOT NULL DEFAULT ''
);
CREATE INDEX sticker_slots_pack ON sticker_slots (pack_id, position);

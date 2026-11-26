-- 小游戏 beyond the board (海龟汤, from nest.core.soup): what the game keeps besides moves, as JSON — the
-- puzzle, its answer (never shown while playing), the questions asked and her answers, the hints given.
ALTER TABLE games ADD COLUMN state TEXT NOT NULL DEFAULT '{}';

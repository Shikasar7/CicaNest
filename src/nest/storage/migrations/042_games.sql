-- 小游戏 (伙伴模式): a board game the user plays with a companion in the desktop app, 五子棋 first.
-- kind: 'gomoku'; moves: JSON [[x, y], …] in order (black first); her_color: 1 black, 2 white; level: how
-- hard she plays ('easy', 'normal', 'hard'); status: 'playing', 'won' (the user won), 'lost', 'draw',
-- 'resigned' (the user gave up), 'left' (a new game was started over it); win: JSON of the five, when there
-- is one; undos: how many times the user took a move back.
CREATE TABLE games (
    id          INTEGER PRIMARY KEY,
    bot_id      TEXT NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
    channel_id  INTEGER NOT NULL REFERENCES channels (id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    her_color   INTEGER NOT NULL,
    level       TEXT NOT NULL,
    moves       TEXT NOT NULL DEFAULT '[]',
    status      TEXT NOT NULL DEFAULT 'playing',
    win         TEXT NOT NULL DEFAULT '[]',
    undos       INTEGER NOT NULL DEFAULT 0,
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    finished_at REAL
);
CREATE INDEX games_bot ON games (bot_id, id);

-- 人设方案: a saved choice of persona for each of a bot's places, so the whole set can be switched in one
-- go and switched back. A persona a bot's set still names cannot be deleted (BotRepo.delete_persona); only
-- the sets of removed bots let go of it.
CREATE TABLE persona_sets (
    id         INTEGER PRIMARY KEY,
    bot_id     TEXT NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
    name       TEXT NOT NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    UNIQUE (bot_id, name)
);

CREATE TABLE persona_set_items (
    set_id     INTEGER NOT NULL REFERENCES persona_sets (id) ON DELETE CASCADE,
    channel_id INTEGER NOT NULL REFERENCES channels (id) ON DELETE CASCADE,
    persona_id INTEGER REFERENCES personas (id) ON DELETE SET NULL,  -- NULL: 不用人设
    PRIMARY KEY (set_id, channel_id)
);

-- A private chat's older talk, summed up: messages up to upto_id are in the summary.
CREATE TABLE channel_memories (
    channel_id INTEGER PRIMARY KEY REFERENCES channels (id) ON DELETE CASCADE,
    summary    TEXT NOT NULL,
    upto_id    INTEGER NOT NULL,
    updated_at REAL NOT NULL
);

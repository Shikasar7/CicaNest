-- 项目资料, step 6: project questions the docs did not answer, forwarded to the
-- person who keeps the docs, and the answers they add (searched like any other section).
ALTER TABLE knowledge_bases ADD COLUMN forward_to TEXT NOT NULL DEFAULT '';  -- QQ number to DM, '' = nobody

CREATE TABLE knowledge_gaps (
    id           INTEGER PRIMARY KEY,
    base_id      INTEGER NOT NULL REFERENCES knowledge_bases (id) ON DELETE CASCADE,
    group_key    TEXT NOT NULL,
    account_id   TEXT NOT NULL,                  -- the bot account that was asked (and forwards it)
    message_id   INTEGER,                        -- the question (messages.id; gone after the history purge)
    asker        TEXT NOT NULL DEFAULT '',
    question     TEXT NOT NULL,
    answer       TEXT NOT NULL DEFAULT '',       -- what the bot said
    reason       TEXT NOT NULL,                  -- "not_found" (retrieval) | "uncertain" (the model said so)
    status       TEXT NOT NULL DEFAULT 'open',   -- "open" | "answered" | "ignored"
    created_at   REAL NOT NULL,
    forwarded_at REAL,
    note_id      INTEGER                          -- the answer added for it
);
CREATE INDEX knowledge_gaps_open ON knowledge_gaps (base_id, status, created_at);

CREATE TABLE knowledge_notes (
    id         INTEGER PRIMARY KEY,
    base_id    INTEGER NOT NULL REFERENCES knowledge_bases (id) ON DELETE CASCADE,
    title      TEXT NOT NULL,
    body       TEXT NOT NULL,
    created_at REAL NOT NULL
);

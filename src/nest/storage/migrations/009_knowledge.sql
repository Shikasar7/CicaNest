-- 项目资料: help docs a bot answers project questions from, which group uses which,
-- and the section vectors (so only new or changed sections are embedded again).
CREATE TABLE knowledge_bases (
    id         INTEGER PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,          -- "my-project"
    folder     TEXT NOT NULL,                 -- a docs/help folder on this machine
    created_at REAL NOT NULL
);

-- A group answers project questions from at most one base.
ALTER TABLE group_features ADD COLUMN knowledge_base_id INTEGER REFERENCES knowledge_bases (id) ON DELETE SET NULL;

CREATE TABLE knowledge_vectors (
    key     TEXT PRIMARY KEY,                 -- sha256 of the model name and the embedded text
    model   TEXT NOT NULL,
    vector  BLOB NOT NULL,                    -- float32, unit length
    used_at REAL NOT NULL                     -- vectors no section uses any more are dropped after a while
);

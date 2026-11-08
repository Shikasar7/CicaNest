-- Console-editable key/value settings (value is JSON).
-- Domain tables (accounts, channels, messages, bots, decisions, llm_calls…) arrive with the milestone that uses them.
CREATE TABLE settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at REAL NOT NULL
);

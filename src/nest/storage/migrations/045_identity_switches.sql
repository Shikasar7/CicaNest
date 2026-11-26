-- When an account changed who it is (人设方案 that keep a name: 阿马 → 阿喵), and the names. What the account
-- said before is then shown to the model as said by the one it was (not 「你」), so the new character does not
-- take the old one's talk as her own. self_id: the account's own id, which its messages carry as sender_id.
CREATE TABLE identity_switches (
    id      INTEGER PRIMARY KEY,
    bot_id  TEXT NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
    self_id TEXT NOT NULL,
    at      REAL NOT NULL,
    before  TEXT NOT NULL,
    after   TEXT NOT NULL
);
CREATE INDEX identity_switches_self ON identity_switches (self_id, at);

-- 桌面群: groups of the user and several companions that exist only in the desk chat. Each
-- member sees the group through a channel of her own (account = her, kind "group", id "desk-g<id>"), like
-- one QQ group seen by several of our accounts; what anyone says is copied into every member's channel.
CREATE TABLE desk_groups (
    id           INTEGER PRIMARY KEY,
    name         TEXT NOT NULL,
    created_by   TEXT NOT NULL,      -- "user", or the bot id of the companion who made it (「她拉的群」)
    created_at   REAL NOT NULL,
    dissolved_at REAL                -- 解散: no longer listed; its history stays in the members' channels
);

CREATE TABLE desk_group_members (
    group_id   INTEGER NOT NULL REFERENCES desk_groups (id) ON DELETE CASCADE,
    bot_id     TEXT NOT NULL REFERENCES bots (id) ON DELETE CASCADE,
    channel_id INTEGER NOT NULL REFERENCES channels (id) ON DELETE CASCADE,
    joined_at  REAL NOT NULL,
    PRIMARY KEY (group_id, bot_id)
);
CREATE INDEX desk_group_members_channel ON desk_group_members (channel_id);

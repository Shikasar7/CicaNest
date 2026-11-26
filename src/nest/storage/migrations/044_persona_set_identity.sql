-- 人设方案 that are a whole character: besides each place's persona, the name she goes by (and is called by),
-- her aliases, her voice and her model are saved with the set and put back when switching to it, so one QQ
-- number can be one character one day and someone else the next. NULL bot_name (sets saved before this): switching
-- leaves all of these as they are.
-- avatar: the picture to put on the account's own profile when switching (QQ: the avatar, and the nickname,
-- which is bot_name); NULL leaves the profile picture alone.
ALTER TABLE persona_sets ADD COLUMN bot_name TEXT;
ALTER TABLE persona_sets ADD COLUMN aliases TEXT;  -- JSON array
ALTER TABLE persona_sets ADD COLUMN voice TEXT;
-- NULL: switching keeps the bot's model (it had none when saved, or that model was deleted since).
ALTER TABLE persona_sets ADD COLUMN provider_id TEXT REFERENCES providers (id) ON DELETE SET NULL;
ALTER TABLE persona_sets ADD COLUMN avatar_type TEXT;
ALTER TABLE persona_sets ADD COLUMN avatar BLOB;

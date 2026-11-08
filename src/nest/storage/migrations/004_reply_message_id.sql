-- The platform message id of what a bot actually sent (live mode), to link a decision to its message.
ALTER TABLE decisions ADD COLUMN reply_message_id TEXT;

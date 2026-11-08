-- What a platform other than QQ needs to connect: plain settings, and under "refs" the names of its
-- secrets in .env. Their http_url and ws_url stay empty: dropping NOT NULL would mean rebuilding a table
-- every other table points at.
ALTER TABLE accounts ADD COLUMN config TEXT NOT NULL DEFAULT '{}';

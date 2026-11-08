-- 欢迎新人: a group switch; when someone joins, one bot there says a short welcome.
ALTER TABLE group_features ADD COLUMN welcome INTEGER NOT NULL DEFAULT 0;

-- 监控触发任务: when a watch finds something, its bot starts a background task with this
-- goal (what was found goes with it); '' : it only tells, as before.
ALTER TABLE watches ADD COLUMN task_goal TEXT NOT NULL DEFAULT '';

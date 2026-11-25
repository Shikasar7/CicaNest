-- 做事模型 (伙伴模式): the model her background tasks and routines run on, when her own
-- model cannot call tools (or another one does that work better). NULL: her own model.
ALTER TABLE bots ADD COLUMN task_provider_id TEXT REFERENCES providers (id) ON DELETE SET NULL;

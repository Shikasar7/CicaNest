-- 用量按用途分: what each model call was for (nest.llm.purpose); '' : something else, or
-- from before.
ALTER TABLE llm_calls ADD COLUMN purpose TEXT NOT NULL DEFAULT '';

-- 项目资料, 看源码: a project's source checkout the bot reads when the docs do
-- not answer, and what it found there for each unanswered question.
ALTER TABLE knowledge_bases ADD COLUMN source_dir TEXT NOT NULL DEFAULT '';  -- '' = no source to read

ALTER TABLE knowledge_gaps ADD COLUMN analysis TEXT NOT NULL DEFAULT '';     -- what the source showed, or ''
ALTER TABLE knowledge_gaps ADD COLUMN analysis_sure INTEGER NOT NULL DEFAULT 0;  -- 1: posted in the group
ALTER TABLE knowledge_gaps ADD COLUMN analysis_files TEXT NOT NULL DEFAULT '';   -- files it rests on, "\n"-joined

-- decisions.trigger gains "source": the follow-up a bot posts after reading the source.

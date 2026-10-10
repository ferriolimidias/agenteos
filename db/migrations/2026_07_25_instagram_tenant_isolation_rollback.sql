-- Rollback for the additive staging migration. Do not run automatically.
ALTER TABLE instagram_comment_executions
    DROP COLUMN IF EXISTS connection_id;

ALTER TABLE instagram_comment_automations
    DROP COLUMN IF EXISTS connection_id;

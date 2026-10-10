-- Additive Instagram-only migration. No audio/follow-up fields are touched.
ALTER TABLE instagram_comment_automations
    ADD COLUMN IF NOT EXISTS instagram_account_id VARCHAR NULL;

UPDATE instagram_comment_automations automation
SET instagram_account_id = connection.instagram_business_account_id
FROM conexoes connection
WHERE automation.connection_id = connection.id
  AND automation.instagram_account_id IS NULL
  AND connection.instagram_business_account_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS ix_ig_comment_automations_empresa_connection_account
    ON instagram_comment_automations (empresa_id, connection_id, instagram_account_id, active);

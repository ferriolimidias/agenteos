-- Additive migration. Apply only through the normal staging/production migration gate.
ALTER TABLE instagram_comment_automations
    ADD COLUMN IF NOT EXISTS connection_id UUID NULL REFERENCES conexoes(id) ON DELETE SET NULL;

ALTER TABLE instagram_comment_executions
    ADD COLUMN IF NOT EXISTS connection_id UUID NULL REFERENCES conexoes(id) ON DELETE SET NULL;

CREATE INDEX IF NOT EXISTS ix_ig_comment_automations_empresa_connection
    ON instagram_comment_automations (empresa_id, connection_id, active);

CREATE INDEX IF NOT EXISTS ix_ig_comment_executions_empresa_connection_created
    ON instagram_comment_executions (empresa_id, connection_id, criado_em DESC);

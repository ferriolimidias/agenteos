-- Roll back schema shape only. Restore values from the verified backup when needed.
DROP INDEX IF EXISTS uq_conexoes_instagram_page_global;
DROP INDEX IF EXISTS uq_conexoes_instagram_account_global;
ALTER TABLE instagram_comment_executions DROP CONSTRAINT IF EXISTS fk_ig_execution_webhook_event;
ALTER TABLE instagram_comment_executions DROP CONSTRAINT IF EXISTS fk_ig_execution_lead;
ALTER TABLE instagram_comment_executions DROP CONSTRAINT IF EXISTS fk_ig_execution_message;
ALTER TABLE instagram_comment_executions DROP COLUMN IF EXISTS webhook_event_id;
ALTER TABLE instagram_comment_executions DROP COLUMN IF EXISTS lead_id;
ALTER TABLE instagram_comment_executions DROP COLUMN IF EXISTS message_id;
ALTER TABLE instagram_comment_executions DROP COLUMN IF EXISTS private_reply;
DROP TABLE IF EXISTS instagram_webhook_events;

ALTER TABLE configuracoes_globais ADD COLUMN IF NOT EXISTS instagram_auth_mode VARCHAR NOT NULL DEFAULT 'instagram_login';
ALTER TABLE configuracoes_globais ADD COLUMN IF NOT EXISTS instagram_app_id VARCHAR NULL;
ALTER TABLE configuracoes_globais ADD COLUMN IF NOT EXISTS instagram_app_secret_encrypted TEXT NULL;
ALTER TABLE configuracoes_globais ADD COLUMN IF NOT EXISTS instagram_verify_token_encrypted TEXT NULL;
ALTER TABLE configuracoes_globais ADD COLUMN IF NOT EXISTS instagram_graph_api_version VARCHAR NOT NULL DEFAULT 'v26.0';
ALTER TABLE configuracoes_globais ADD COLUMN IF NOT EXISTS instagram_config_updated_at TIMESTAMP WITHOUT TIME ZONE NULL;

CREATE TABLE IF NOT EXISTS instagram_authorized_profiles (
    id UUID PRIMARY KEY,
    instagram_account_id VARCHAR NOT NULL,
    username VARCHAR NOT NULL,
    encrypted_access_token TEXT NOT NULL,
    permissions JSONB NOT NULL DEFAULT '[]'::jsonb,
    expires_at TIMESTAMP WITHOUT TIME ZONE NULL,
    last_validated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'valid',
    auth_source VARCHAR NOT NULL DEFAULT 'manual',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    criado_em TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    atualizado_em TIMESTAMP WITHOUT TIME ZONE NOT NULL
);

ALTER TABLE conexoes ADD COLUMN IF NOT EXISTS authorized_instagram_profile_id UUID NULL;
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_conexoes_instagram_authorized_profile') THEN
        ALTER TABLE conexoes ADD CONSTRAINT fk_conexoes_instagram_authorized_profile
            FOREIGN KEY (authorized_instagram_profile_id) REFERENCES instagram_authorized_profiles(id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_conexoes_authorized_instagram_profile') THEN
        ALTER TABLE conexoes ADD CONSTRAINT uq_conexoes_authorized_instagram_profile
            UNIQUE (authorized_instagram_profile_id);
    END IF;
END $$;

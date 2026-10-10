-- Additive global configuration for Instagram API with Instagram Login.
-- Existing connection columns and data remain untouched for rollback safety.
ALTER TABLE configuracoes_globais
    ADD COLUMN IF NOT EXISTS instagram_app_id VARCHAR NULL,
    ADD COLUMN IF NOT EXISTS instagram_app_secret_encrypted TEXT NULL,
    ADD COLUMN IF NOT EXISTS instagram_verify_token_encrypted TEXT NULL,
    ADD COLUMN IF NOT EXISTS instagram_graph_api_version VARCHAR NOT NULL DEFAULT 'v26.0',
    ADD COLUMN IF NOT EXISTS instagram_config_updated_at TIMESTAMP WITHOUT TIME ZONE NULL;

ALTER TABLE configuracoes_globais
    DROP CONSTRAINT IF EXISTS ck_configuracoes_globais_instagram_graph_v26;
ALTER TABLE configuracoes_globais
    ADD CONSTRAINT ck_configuracoes_globais_instagram_graph_v26
    CHECK (instagram_graph_api_version = 'v26.0');

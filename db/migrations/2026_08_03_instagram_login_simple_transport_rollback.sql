-- Roll back only the additive Instagram Login global configuration.
-- Connection data and legacy columns are deliberately preserved.
ALTER TABLE configuracoes_globais
    DROP CONSTRAINT IF EXISTS ck_configuracoes_globais_instagram_graph_v26;
ALTER TABLE configuracoes_globais
    DROP COLUMN IF EXISTS instagram_config_updated_at,
    DROP COLUMN IF EXISTS instagram_graph_api_version,
    DROP COLUMN IF EXISTS instagram_verify_token_encrypted,
    DROP COLUMN IF EXISTS instagram_app_secret_encrypted,
    DROP COLUMN IF EXISTS instagram_app_id;

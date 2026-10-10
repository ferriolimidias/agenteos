-- Instagram Simple Page Flow. Apply only after the verified 2026-08-02 backup.
-- The Graph version is a runtime constant (v26.0), not a database setting.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
    IF to_regclass('public.conexoes') IS NULL
       OR to_regclass('public.instagram_comment_automations') IS NULL
       OR to_regclass('public.instagram_comment_executions') IS NULL THEN
        RAISE EXCEPTION 'precheck failed: Instagram persistence dependencies are absent';
    END IF;
END $$;

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM conexoes
        WHERE tipo = 'INSTAGRAM'::tipo_conexao_enum AND page_id IS NOT NULL
        GROUP BY page_id HAVING count(*) > 1
    ) OR EXISTS (
        SELECT 1 FROM conexoes
        WHERE tipo = 'INSTAGRAM'::tipo_conexao_enum AND instagram_business_account_id IS NOT NULL
        GROUP BY instagram_business_account_id HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'precheck failed: ambiguous Instagram Page/account assignment across connections';
    END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_conexoes_instagram_page_global
    ON conexoes (page_id) WHERE tipo = 'INSTAGRAM'::tipo_conexao_enum AND page_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_conexoes_instagram_account_global
    ON conexoes (instagram_business_account_id)
    WHERE tipo = 'INSTAGRAM'::tipo_conexao_enum AND instagram_business_account_id IS NOT NULL;

ALTER TABLE instagram_comment_executions ADD COLUMN IF NOT EXISTS private_reply TEXT NULL;
ALTER TABLE instagram_comment_executions ADD COLUMN IF NOT EXISTS webhook_event_id UUID NULL;
ALTER TABLE instagram_comment_executions ADD COLUMN IF NOT EXISTS lead_id UUID NULL;
ALTER TABLE instagram_comment_executions ADD COLUMN IF NOT EXISTS message_id UUID NULL;

CREATE TABLE IF NOT EXISTS instagram_webhook_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    connection_id UUID NOT NULL REFERENCES conexoes(id) ON DELETE CASCADE,
    comment_id VARCHAR NOT NULL,
    media_id VARCHAR NULL,
    sender_id VARCHAR NULL,
    status VARCHAR NOT NULL DEFAULT 'received',
    error_detail TEXT NULL,
    criado_em TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT uq_instagram_webhook_event_comment UNIQUE (connection_id, comment_id)
);
CREATE INDEX IF NOT EXISTS ix_instagram_webhook_events_empresa_created
    ON instagram_webhook_events (empresa_id, criado_em DESC);

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_ig_execution_webhook_event') THEN
        ALTER TABLE instagram_comment_executions ADD CONSTRAINT fk_ig_execution_webhook_event
            FOREIGN KEY (webhook_event_id) REFERENCES instagram_webhook_events(id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_ig_execution_lead') THEN
        ALTER TABLE instagram_comment_executions ADD CONSTRAINT fk_ig_execution_lead
            FOREIGN KEY (lead_id) REFERENCES crm_leads(id) ON DELETE SET NULL;
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_ig_execution_message') THEN
        ALTER TABLE instagram_comment_executions ADD CONSTRAINT fk_ig_execution_message
            FOREIGN KEY (message_id) REFERENCES mensagens_historico(id) ON DELETE SET NULL;
    END IF;
END $$;

ALTER TABLE conexoes DROP CONSTRAINT IF EXISTS fk_conexoes_instagram_authorized_profile;
ALTER TABLE conexoes DROP CONSTRAINT IF EXISTS uq_conexoes_authorized_instagram_profile;
ALTER TABLE conexoes DROP COLUMN IF EXISTS authorized_instagram_profile_id;
DROP TABLE IF EXISTS instagram_authorized_profiles;

ALTER TABLE configuracoes_globais DROP COLUMN IF EXISTS instagram_auth_mode;
ALTER TABLE configuracoes_globais DROP COLUMN IF EXISTS instagram_app_id;
ALTER TABLE configuracoes_globais DROP COLUMN IF EXISTS instagram_app_secret_encrypted;
ALTER TABLE configuracoes_globais DROP COLUMN IF EXISTS instagram_verify_token_encrypted;
ALTER TABLE configuracoes_globais DROP COLUMN IF EXISTS instagram_graph_api_version;
ALTER TABLE configuracoes_globais DROP COLUMN IF EXISTS instagram_config_updated_at;

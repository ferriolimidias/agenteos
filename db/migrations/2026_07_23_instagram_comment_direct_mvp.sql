CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS instagram_comment_automations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    name VARCHAR NOT NULL,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    media_id VARCHAR NULL,
    keywords JSONB NOT NULL DEFAULT '[]'::jsonb,
    public_reply_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    public_reply_mode VARCHAR NOT NULL DEFAULT 'fixed',
    fixed_reply TEXT NULL,
    ai_examples TEXT NULL,
    tone_instructions TEXT NULL,
    fallback_reply TEXT NOT NULL,
    private_reply_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    private_message TEXT NULL,
    private_link TEXT NULL,
    recent_public_replies JSONB NOT NULL DEFAULT '[]'::jsonb,
    criado_em TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_ig_comment_automations_empresa_active ON instagram_comment_automations (empresa_id, active);

CREATE TABLE IF NOT EXISTS instagram_comment_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    automation_id UUID NOT NULL REFERENCES instagram_comment_automations(id) ON DELETE CASCADE,
    instagram_account_id VARCHAR NOT NULL,
    comment_id VARCHAR NOT NULL,
    media_id VARCHAR NULL,
    username VARCHAR NULL,
    comment_text TEXT NULL,
    public_reply TEXT NULL,
    public_reply_origin VARCHAR NULL,
    public_reply_status VARCHAR NOT NULL DEFAULT 'public_reply_pending',
    private_reply_status VARCHAR NOT NULL DEFAULT 'private_reply_pending',
    status VARCHAR NOT NULL DEFAULT 'pending',
    error_detail TEXT NULL,
    criado_em TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
    atualizado_em TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT uq_ig_comment_execution_idempotency UNIQUE (empresa_id, instagram_account_id, comment_id, automation_id)
);
CREATE INDEX IF NOT EXISTS ix_ig_comment_executions_empresa_created ON instagram_comment_executions (empresa_id, criado_em DESC);

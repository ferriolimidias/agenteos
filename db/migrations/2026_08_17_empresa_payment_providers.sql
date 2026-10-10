CREATE TABLE IF NOT EXISTS empresa_payment_providers (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    provider VARCHAR(40) NOT NULL,
    environment VARCHAR(20) NOT NULL DEFAULT 'test',
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    access_token_encrypted TEXT NULL,
    webhook_secret_encrypted TEXT NULL,
    criado_em TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    atualizado_em TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_empresa_payment_providers_empresa_provider UNIQUE (empresa_id, provider),
    CONSTRAINT ck_empresa_payment_providers_provider CHECK (provider IN ('mercado_pago')),
    CONSTRAINT ck_empresa_payment_providers_environment CHECK (environment IN ('test', 'production'))
);

CREATE INDEX IF NOT EXISTS ix_empresa_payment_providers_empresa_enabled
    ON empresa_payment_providers (empresa_id, enabled);

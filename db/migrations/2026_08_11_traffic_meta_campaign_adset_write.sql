DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conname = 'uq_traffic_meta_configs_empresa_id_id'
    ) THEN
        ALTER TABLE traffic_meta_configs
        ADD CONSTRAINT uq_traffic_meta_configs_empresa_id_id UNIQUE (empresa_id, id);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS traffic_meta_campaign_creations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    traffic_meta_config_id UUID NOT NULL,
    idempotency_key VARCHAR NOT NULL,
    name VARCHAR NOT NULL,
    adset_name VARCHAR NOT NULL,
    daily_budget_cents INTEGER NOT NULL,
    location_type VARCHAR NOT NULL,
    page_id VARCHAR NOT NULL,
    phone_number_id VARCHAR NOT NULL,
    meta_campaign_id VARCHAR NULL,
    meta_adset_id VARCHAR NULL,
    campaign_status VARCHAR NULL,
    adset_status VARCHAR NULL,
    status VARCHAR NOT NULL DEFAULT 'pending',
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    response_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    error_json JSONB NULL,
    created_by_user_id UUID NULL REFERENCES usuarios(id) ON DELETE SET NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT uq_traffic_meta_campaign_creations_idempotency UNIQUE (empresa_id, idempotency_key),
    CONSTRAINT fk_traffic_meta_creation_empresa_config
        FOREIGN KEY (empresa_id, traffic_meta_config_id)
        REFERENCES traffic_meta_configs(empresa_id, id)
);

CREATE INDEX IF NOT EXISTS ix_traffic_meta_campaign_creations_empresa_status
ON traffic_meta_campaign_creations (empresa_id, status);

CREATE INDEX IF NOT EXISTS ix_traffic_meta_campaign_creations_meta_campaign
ON traffic_meta_campaign_creations (empresa_id, meta_campaign_id);

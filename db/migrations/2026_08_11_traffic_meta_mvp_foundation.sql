CREATE TABLE IF NOT EXISTS traffic_meta_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    business_id VARCHAR NULL,
    ad_account_id VARCHAR NULL,
    page_id VARCHAR NULL,
    whatsapp_connection_id UUID NULL,
    waba_id VARCHAR NULL,
    phone_number_id VARCHAR NULL,
    dataset_id VARCHAR NULL,
    status VARCHAR NOT NULL DEFAULT 'draft',
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
    CONSTRAINT uq_traffic_meta_configs_empresa_id UNIQUE (empresa_id),
    CONSTRAINT uq_traffic_meta_configs_ad_account_id UNIQUE (ad_account_id),
    CONSTRAINT fk_traffic_meta_config_empresa_whatsapp_connection
        FOREIGN KEY (empresa_id, whatsapp_connection_id)
        REFERENCES conexoes(empresa_id, id)
);

CREATE INDEX IF NOT EXISTS ix_traffic_meta_configs_empresa_ad_account
ON traffic_meta_configs (empresa_id, ad_account_id);

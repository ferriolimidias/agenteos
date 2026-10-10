ALTER TABLE traffic_meta_configs
    ADD COLUMN IF NOT EXISTS instagram_account_id VARCHAR NULL;

ALTER TABLE traffic_meta_campaign_creations
    ADD COLUMN IF NOT EXISTS business_id VARCHAR NULL,
    ADD COLUMN IF NOT EXISTS instagram_account_id VARCHAR NULL,
    ADD COLUMN IF NOT EXISTS whatsapp_phone_number VARCHAR NULL,
    ADD COLUMN IF NOT EXISTS waba_id VARCHAR NULL,
    ADD COLUMN IF NOT EXISTS dataset_id VARCHAR NULL;

CREATE INDEX IF NOT EXISTS ix_traffic_meta_campaign_creations_empresa_page
    ON traffic_meta_campaign_creations (empresa_id, page_id);

CREATE INDEX IF NOT EXISTS ix_traffic_meta_campaign_creations_empresa_dataset
    ON traffic_meta_campaign_creations (empresa_id, dataset_id);

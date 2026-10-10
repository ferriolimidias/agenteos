ALTER TABLE traffic_meta_campaign_creations
    ADD COLUMN IF NOT EXISTS ad_account_id VARCHAR NULL,
    ADD COLUMN IF NOT EXISTS whatsapp_connection_id UUID NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_traffic_meta_creation_empresa_whatsapp_connection'
    ) THEN
        ALTER TABLE traffic_meta_campaign_creations
        ADD CONSTRAINT fk_traffic_meta_creation_empresa_whatsapp_connection
        FOREIGN KEY (empresa_id, whatsapp_connection_id)
        REFERENCES conexoes(empresa_id, id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_traffic_meta_campaign_creations_empresa_whatsapp
    ON traffic_meta_campaign_creations (empresa_id, whatsapp_connection_id);

CREATE INDEX IF NOT EXISTS ix_traffic_meta_campaign_creations_empresa_ad_account
    ON traffic_meta_campaign_creations (empresa_id, ad_account_id);

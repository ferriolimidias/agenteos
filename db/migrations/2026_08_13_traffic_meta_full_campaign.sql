ALTER TABLE traffic_meta_campaign_creations
    ADD COLUMN IF NOT EXISTS campaign_sequence INTEGER;

CREATE INDEX IF NOT EXISTS ix_traffic_meta_campaign_creations_empresa_sequence
    ON traffic_meta_campaign_creations (empresa_id, campaign_sequence);

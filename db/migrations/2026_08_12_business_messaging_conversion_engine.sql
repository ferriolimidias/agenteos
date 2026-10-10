CREATE TABLE IF NOT EXISTS business_messaging_conversion_mappings (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    trigger_type VARCHAR NOT NULL,
    trigger_ref VARCHAR NOT NULL,
    event_name VARCHAR NOT NULL,
    ativo BOOLEAN NOT NULL DEFAULT FALSE,
    send_mode VARCHAR NOT NULL DEFAULT 'dry_run',
    default_value DOUBLE PRECISION NULL,
    currency VARCHAR NOT NULL DEFAULT 'BRL',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    criado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    atualizado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    CONSTRAINT uq_bm_conversion_mapping_empresa_trigger UNIQUE (empresa_id, trigger_type, trigger_ref)
);

CREATE INDEX IF NOT EXISTS ix_bm_conversion_mappings_empresa_active
ON business_messaging_conversion_mappings (empresa_id, ativo);

ALTER TABLE meta_conversion_events ADD COLUMN IF NOT EXISTS trigger_type VARCHAR NULL;
ALTER TABLE meta_conversion_events ADD COLUMN IF NOT EXISTS trigger_ref VARCHAR NULL;
ALTER TABLE meta_conversion_events ADD COLUMN IF NOT EXISTS trigger_source_event_id VARCHAR NULL;

CREATE INDEX IF NOT EXISTS ix_meta_conversion_events_empresa_trigger
ON meta_conversion_events (empresa_id, trigger_type, trigger_ref);

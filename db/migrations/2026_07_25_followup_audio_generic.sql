BEGIN;

CREATE TABLE IF NOT EXISTS media_assets (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    media_type VARCHAR NOT NULL DEFAULT 'audio',
    original_name VARCHAR NOT NULL,
    mime_type VARCHAR NOT NULL,
    byte_size INTEGER NOT NULL,
    content_hash VARCHAR NOT NULL,
    storage_key VARCHAR NOT NULL,
    duration_ms INTEGER NULL,
    status VARCHAR NOT NULL DEFAULT 'ready',
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    criado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    atualizado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    CONSTRAINT uq_media_assets_empresa_hash UNIQUE (empresa_id, content_hash)
);

CREATE INDEX IF NOT EXISTS ix_media_assets_empresa_status
    ON media_assets (empresa_id, status, media_type);

ALTER TABLE followup_sequence_steps
    ADD COLUMN IF NOT EXISTS content_type VARCHAR NOT NULL DEFAULT 'text';
ALTER TABLE followup_sequence_steps
    ADD COLUMN IF NOT EXISTS media_asset_id UUID NULL REFERENCES media_assets(id) ON DELETE SET NULL;
ALTER TABLE followup_sequence_steps
    ADD COLUMN IF NOT EXISTS send_as_voice BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE followup_sequence_steps
    ADD COLUMN IF NOT EXISTS audio_caption TEXT NULL;

CREATE INDEX IF NOT EXISTS ix_followup_sequence_steps_media
    ON followup_sequence_steps (empresa_id, media_asset_id, content_type);

COMMIT;

-- Campaign Central real send bridge.
-- Additive migration: keeps legacy campanhas_disparo enum/status intact and
-- stores the new Campaign Central runtime state in explicit columns.

ALTER TABLE campanhas_disparo
  ADD COLUMN IF NOT EXISTS central_status VARCHAR NOT NULL DEFAULT 'draft',
  ADD COLUMN IF NOT EXISTS conexao_id UUID NULL,
  ADD COLUMN IF NOT EXISTS meta_template_id UUID NULL,
  ADD COLUMN IF NOT EXISTS payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  ADD COLUMN IF NOT EXISTS started_at TIMESTAMP NULL,
  ADD COLUMN IF NOT EXISTS queued_at TIMESTAMP NULL,
  ADD COLUMN IF NOT EXISTS paused_at TIMESTAMP NULL,
  ADD COLUMN IF NOT EXISTS resumed_at TIMESTAMP NULL,
  ADD COLUMN IF NOT EXISTS completed_at TIMESTAMP NULL,
  ADD COLUMN IF NOT EXISTS cancelled_at TIMESTAMP NULL,
  ADD COLUMN IF NOT EXISTS failed_at TIMESTAMP NULL,
  ADD COLUMN IF NOT EXISTS error_code VARCHAR NULL,
  ADD COLUMN IF NOT EXISTS error_detail TEXT NULL;

ALTER TABLE campaign_recipients
  ADD COLUMN IF NOT EXISTS meta_template_id UUID NULL,
  ADD COLUMN IF NOT EXISTS payload_json JSONB NOT NULL DEFAULT '{}'::jsonb;

CREATE INDEX IF NOT EXISTS ix_campanhas_disparo_central_status
  ON campanhas_disparo (empresa_id, central_status, criado_em);

CREATE INDEX IF NOT EXISTS ix_campanhas_disparo_conexao
  ON campanhas_disparo (empresa_id, conexao_id);

CREATE INDEX IF NOT EXISTS ix_campaign_recipients_meta_template
  ON campaign_recipients (empresa_id, meta_template_id);

CREATE INDEX IF NOT EXISTS ix_message_outbox_campaign_claim
  ON message_outbox (status, available_at, priority, locked_at, campaign_recipient_id)
  WHERE campaign_recipient_id IS NOT NULL;


CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
    IF to_regclass('public.empresas') IS NULL THEN
        RAISE EXCEPTION 'precheck failed: table empresas is required';
    END IF;
    IF to_regclass('public.conexoes') IS NULL THEN
        RAISE EXCEPTION 'precheck failed: table conexoes is required';
    END IF;
    IF to_regclass('public.channel_contacts') IS NULL THEN
        RAISE EXCEPTION 'precheck failed: phase2 channel_contacts is required';
    END IF;
    IF to_regclass('public.channel_conversations') IS NULL THEN
        RAISE EXCEPTION 'precheck failed: phase2 channel_conversations is required';
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_campanhas_disparo_empresa_id_id') THEN
        ALTER TABLE campanhas_disparo
            ADD CONSTRAINT uq_campanhas_disparo_empresa_id_id UNIQUE (empresa_id, id);
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_config_followups_empresa_id_id') THEN
        ALTER TABLE config_followups
            ADD CONSTRAINT uq_config_followups_empresa_id_id UNIQUE (empresa_id, id);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS campaign_recipients (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    campanha_id UUID NOT NULL REFERENCES campanhas_disparo(id) ON DELETE CASCADE,
    conexao_id UUID NOT NULL REFERENCES conexoes(id) ON DELETE RESTRICT,
    provider VARCHAR NOT NULL,
    channel VARCHAR NOT NULL,
    lead_id UUID NULL REFERENCES crm_leads(id) ON DELETE SET NULL,
    contact_id UUID NOT NULL REFERENCES channel_contacts(id) ON DELETE RESTRICT,
    conversation_id UUID NULL REFERENCES channel_conversations(id) ON DELETE SET NULL,
    recipient_address VARCHAR NOT NULL,
    template_id UUID NULL REFERENCES templates_mensagem(id) ON DELETE SET NULL,
    parameters_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status VARCHAR NOT NULL DEFAULT 'queued',
    scheduled_at TIMESTAMP WITHOUT TIME ZONE NULL,
    processing_started_at TIMESTAMP WITHOUT TIME ZONE NULL,
    sent_at TIMESTAMP WITHOUT TIME ZONE NULL,
    delivered_at TIMESTAMP WITHOUT TIME ZONE NULL,
    read_at TIMESTAMP WITHOUT TIME ZONE NULL,
    failed_at TIMESTAMP WITHOUT TIME ZONE NULL,
    cancelled_at TIMESTAMP WITHOUT TIME ZONE NULL,
    external_message_id VARCHAR NULL,
    error_code VARCHAR NULL,
    error_detail TEXT NULL,
    attempt_count INTEGER NOT NULL DEFAULT 0,
    idempotency_key VARCHAR NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    CONSTRAINT fk_campaign_recipients_empresa_campanha FOREIGN KEY (empresa_id, campanha_id) REFERENCES campanhas_disparo(empresa_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_campaign_recipients_empresa_conexao FOREIGN KEY (empresa_id, conexao_id) REFERENCES conexoes(empresa_id, id),
    CONSTRAINT fk_campaign_recipients_empresa_lead FOREIGN KEY (empresa_id, lead_id) REFERENCES crm_leads(empresa_id, id),
    CONSTRAINT fk_campaign_recipients_empresa_contact FOREIGN KEY (empresa_id, contact_id) REFERENCES channel_contacts(empresa_id, id),
    CONSTRAINT fk_campaign_recipients_empresa_conversation FOREIGN KEY (empresa_id, conversation_id) REFERENCES channel_conversations(empresa_id, id),
    CONSTRAINT uq_campaign_recipients_empresa_id_id UNIQUE (empresa_id, id),
    CONSTRAINT uq_campaign_recipients_idempotency UNIQUE (empresa_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS followup_executions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    followup_id UUID NOT NULL REFERENCES config_followups(id) ON DELETE CASCADE,
    lead_id UUID NOT NULL REFERENCES crm_leads(id) ON DELETE CASCADE,
    contact_id UUID NOT NULL REFERENCES channel_contacts(id) ON DELETE RESTRICT,
    conversation_id UUID NOT NULL REFERENCES channel_conversations(id) ON DELETE RESTRICT,
    conexao_id UUID NOT NULL REFERENCES conexoes(id) ON DELETE RESTRICT,
    provider VARCHAR NOT NULL,
    channel VARCHAR NOT NULL,
    trigger VARCHAR NOT NULL,
    scheduled_at TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'scheduled',
    stop_reason VARCHAR NULL,
    template_id UUID NULL REFERENCES templates_mensagem(id) ON DELETE SET NULL,
    idempotency_key VARCHAR NOT NULL,
    outbox_id UUID NULL,
    external_message_id VARCHAR NULL,
    processing_started_at TIMESTAMP WITHOUT TIME ZONE NULL,
    sent_at TIMESTAMP WITHOUT TIME ZONE NULL,
    completed_at TIMESTAMP WITHOUT TIME ZONE NULL,
    cancelled_at TIMESTAMP WITHOUT TIME ZONE NULL,
    failed_at TIMESTAMP WITHOUT TIME ZONE NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    CONSTRAINT fk_followup_executions_empresa_followup FOREIGN KEY (empresa_id, followup_id) REFERENCES config_followups(empresa_id, id) ON DELETE CASCADE,
    CONSTRAINT fk_followup_executions_empresa_lead FOREIGN KEY (empresa_id, lead_id) REFERENCES crm_leads(empresa_id, id),
    CONSTRAINT fk_followup_executions_empresa_contact FOREIGN KEY (empresa_id, contact_id) REFERENCES channel_contacts(empresa_id, id),
    CONSTRAINT fk_followup_executions_empresa_conversation FOREIGN KEY (empresa_id, conversation_id) REFERENCES channel_conversations(empresa_id, id),
    CONSTRAINT fk_followup_executions_empresa_conexao FOREIGN KEY (empresa_id, conexao_id) REFERENCES conexoes(empresa_id, id),
    CONSTRAINT uq_followup_executions_empresa_id_id UNIQUE (empresa_id, id),
    CONSTRAINT uq_followup_executions_idempotency UNIQUE (empresa_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS message_outbox (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    conexao_id UUID NOT NULL REFERENCES conexoes(id) ON DELETE RESTRICT,
    provider VARCHAR NOT NULL,
    channel VARCHAR NOT NULL,
    operation_type VARCHAR NOT NULL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key VARCHAR NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'queued',
    priority INTEGER NOT NULL DEFAULT 100,
    available_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
    locked_at TIMESTAMP WITHOUT TIME ZONE NULL,
    locked_by VARCHAR NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL DEFAULT 3,
    last_error_code VARCHAR NULL,
    last_error_detail TEXT NULL,
    external_message_id VARCHAR NULL,
    campaign_recipient_id UUID NULL REFERENCES campaign_recipients(id) ON DELETE SET NULL,
    followup_execution_id UUID NULL REFERENCES followup_executions(id) ON DELETE SET NULL,
    completed_at TIMESTAMP WITHOUT TIME ZONE NULL,
    cancelled_at TIMESTAMP WITHOUT TIME ZONE NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    CONSTRAINT fk_message_outbox_empresa_conexao FOREIGN KEY (empresa_id, conexao_id) REFERENCES conexoes(empresa_id, id),
    CONSTRAINT fk_message_outbox_empresa_campaign_recipient FOREIGN KEY (empresa_id, campaign_recipient_id) REFERENCES campaign_recipients(empresa_id, id),
    CONSTRAINT fk_message_outbox_empresa_followup_execution FOREIGN KEY (empresa_id, followup_execution_id) REFERENCES followup_executions(empresa_id, id),
    CONSTRAINT uq_message_outbox_empresa_id_id UNIQUE (empresa_id, id),
    CONSTRAINT uq_message_outbox_idempotency UNIQUE (empresa_id, idempotency_key)
);

CREATE TABLE IF NOT EXISTS message_attempts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    outbox_id UUID NOT NULL REFERENCES message_outbox(id) ON DELETE CASCADE,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    provider VARCHAR NOT NULL,
    conexao_id UUID NOT NULL REFERENCES conexoes(id) ON DELETE RESTRICT,
    attempt_number INTEGER NOT NULL,
    started_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT now(),
    finished_at TIMESTAMP WITHOUT TIME ZONE NULL,
    result_status VARCHAR NOT NULL DEFAULT 'started',
    external_message_id VARCHAR NULL,
    error_code VARCHAR NULL,
    error_detail TEXT NULL,
    duration_ms INTEGER NULL,
    CONSTRAINT fk_message_attempts_empresa_outbox FOREIGN KEY (empresa_id, outbox_id) REFERENCES message_outbox(empresa_id, id),
    CONSTRAINT uq_message_attempts_attempt UNIQUE (empresa_id, outbox_id, attempt_number)
);

CREATE TABLE IF NOT EXISTS communication_consents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    lead_id UUID NULL REFERENCES crm_leads(id) ON DELETE SET NULL,
    contact_id UUID NOT NULL REFERENCES channel_contacts(id) ON DELETE CASCADE,
    conexao_id UUID NULL REFERENCES conexoes(id) ON DELETE CASCADE,
    channel VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    status VARCHAR NOT NULL DEFAULT 'unknown',
    source VARCHAR NOT NULL DEFAULT 'unknown',
    proof_reference TEXT NULL,
    reason TEXT NULL,
    recorded_by_user_id UUID NULL REFERENCES usuarios(id) ON DELETE SET NULL,
    consented_at TIMESTAMP WITHOUT TIME ZONE NULL,
    revoked_at TIMESTAMP WITHOUT TIME ZONE NULL,
    blocked_at TIMESTAMP WITHOUT TIME ZONE NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    updated_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    CONSTRAINT fk_communication_consents_empresa_lead FOREIGN KEY (empresa_id, lead_id) REFERENCES crm_leads(empresa_id, id),
    CONSTRAINT fk_communication_consents_empresa_contact FOREIGN KEY (empresa_id, contact_id) REFERENCES channel_contacts(empresa_id, id),
    CONSTRAINT fk_communication_consents_empresa_conexao FOREIGN KEY (empresa_id, conexao_id) REFERENCES conexoes(empresa_id, id),
    CONSTRAINT uq_communication_consents_scope UNIQUE (empresa_id, channel, provider, contact_id, conexao_id),
    CONSTRAINT ck_communication_consents_status CHECK (status IN ('unknown', 'opted_in', 'opted_out', 'blocked', 'transactional_only'))
);

CREATE TABLE IF NOT EXISTS message_status_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    conexao_id UUID NOT NULL REFERENCES conexoes(id) ON DELETE RESTRICT,
    provider VARCHAR NOT NULL,
    channel VARCHAR NOT NULL,
    external_message_id VARCHAR NULL,
    external_event_id VARCHAR NOT NULL,
    status VARCHAR NOT NULL,
    occurred_at TIMESTAMP WITHOUT TIME ZONE NULL,
    raw_status VARCHAR NULL,
    error_code VARCHAR NULL,
    error_detail TEXT NULL,
    idempotency_key VARCHAR NOT NULL,
    created_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    CONSTRAINT fk_message_status_events_empresa_conexao FOREIGN KEY (empresa_id, conexao_id) REFERENCES conexoes(empresa_id, id),
    CONSTRAINT uq_message_status_events_idempotency UNIQUE (empresa_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_campaign_recipients_campaign_status ON campaign_recipients (empresa_id, campanha_id, status);
CREATE INDEX IF NOT EXISTS ix_campaign_recipients_external_message ON campaign_recipients (empresa_id, provider, external_message_id);
CREATE INDEX IF NOT EXISTS ix_followup_executions_status ON followup_executions (empresa_id, status, scheduled_at);
CREATE INDEX IF NOT EXISTS ix_message_outbox_claim ON message_outbox (status, available_at, priority, locked_at);
CREATE INDEX IF NOT EXISTS ix_message_outbox_external_message ON message_outbox (empresa_id, provider, external_message_id);
CREATE INDEX IF NOT EXISTS ix_message_attempts_outbox ON message_attempts (empresa_id, outbox_id);
CREATE INDEX IF NOT EXISTS ix_communication_consents_lookup ON communication_consents (empresa_id, channel, provider, status);
CREATE INDEX IF NOT EXISTS ix_message_status_events_external ON message_status_events (empresa_id, provider, external_message_id, status);

DO $$
BEGIN
    IF to_regclass('public.message_outbox') IS NULL THEN
        RAISE EXCEPTION 'postcheck failed: message_outbox missing';
    END IF;
    IF to_regclass('public.campaign_recipients') IS NULL THEN
        RAISE EXCEPTION 'postcheck failed: campaign_recipients missing';
    END IF;
    IF to_regclass('public.followup_executions') IS NULL THEN
        RAISE EXCEPTION 'postcheck failed: followup_executions missing';
    END IF;
END $$;

-- Canonical attribution facts and separately refreshable interpretations.
-- This migration is additive. Legacy JSON and whatsapp_ad_attributions remain intact.

CREATE UNIQUE INDEX IF NOT EXISTS uq_mensagens_historico_empresa_id_id
    ON mensagens_historico (empresa_id, id);

CREATE TABLE IF NOT EXISTS attribution_touches (
    id uuid PRIMARY KEY,
    empresa_id uuid NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    provider text NOT NULL DEFAULT 'unknown',
    channel text NOT NULL DEFAULT 'unknown',
    connection_id uuid NULL,
    touch_type text NOT NULL,
    touch_source text NOT NULL,
    event_kind text NOT NULL,
    occurred_at timestamptz NOT NULL,
    received_at timestamptz NOT NULL DEFAULT now(),
    contact_id uuid NULL,
    conversation_id uuid NULL,
    lead_id uuid NULL,
    message_id uuid NULL,
    external_event_id text NULL,
    ctwa_clid text NULL,
    source_id text NULL,
    source_url text NULL,
    source_type text NULL,
    headline varchar(240) NULL,
    body text NULL,
    media_type varchar(40) NULL,
    outbound_campaign_id uuid NULL,
    campaign_recipient_id uuid NULL,
    checkout_id text NULL,
    payment_id text NULL,
    attribution_touch_id uuid NULL,
    amount numeric(18, 6) NULL,
    currency varchar(3) NULL,
    metadata_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    idempotency_key varchar(128) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_attribution_touches_empresa_id_id UNIQUE (empresa_id, id),
    CONSTRAINT uq_attribution_touches_idempotency UNIQUE (empresa_id, idempotency_key),
    CONSTRAINT ck_attribution_touches_amount_currency CHECK (
        (amount IS NULL AND currency IS NULL) OR
        (amount IS NOT NULL AND currency IS NOT NULL AND char_length(currency) = 3)
    ),
    CONSTRAINT ck_attribution_touches_metadata_object CHECK (jsonb_typeof(metadata_json) = 'object'),
    CONSTRAINT fk_attribution_touches_empresa_connection
        FOREIGN KEY (empresa_id, connection_id) REFERENCES conexoes(empresa_id, id),
    CONSTRAINT fk_attribution_touches_empresa_contact
        FOREIGN KEY (empresa_id, contact_id) REFERENCES channel_contacts(empresa_id, id),
    CONSTRAINT fk_attribution_touches_empresa_conversation
        FOREIGN KEY (empresa_id, conversation_id) REFERENCES channel_conversations(empresa_id, id),
    CONSTRAINT fk_attribution_touches_empresa_lead
        FOREIGN KEY (empresa_id, lead_id) REFERENCES crm_leads(empresa_id, id),
    CONSTRAINT fk_attribution_touches_empresa_message
        FOREIGN KEY (empresa_id, message_id) REFERENCES mensagens_historico(empresa_id, id),
    CONSTRAINT fk_attribution_touches_empresa_campaign
        FOREIGN KEY (empresa_id, outbound_campaign_id) REFERENCES campanhas_disparo(empresa_id, id),
    CONSTRAINT fk_attribution_touches_empresa_recipient
        FOREIGN KEY (empresa_id, campaign_recipient_id) REFERENCES campaign_recipients(empresa_id, id),
    CONSTRAINT fk_attribution_touches_empresa_attribution_touch
        FOREIGN KEY (empresa_id, attribution_touch_id) REFERENCES attribution_touches(empresa_id, id)
);

ALTER TABLE attribution_touches ADD COLUMN IF NOT EXISTS headline varchar(240) NULL;
ALTER TABLE attribution_touches ADD COLUMN IF NOT EXISTS body text NULL;
ALTER TABLE attribution_touches ADD COLUMN IF NOT EXISTS media_type varchar(40) NULL;

CREATE INDEX IF NOT EXISTS ix_attribution_touches_lead_time
    ON attribution_touches (empresa_id, lead_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_attribution_touches_conversation_time
    ON attribution_touches (empresa_id, conversation_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_attribution_touches_campaign_time
    ON attribution_touches (empresa_id, outbound_campaign_id, occurred_at);
CREATE INDEX IF NOT EXISTS ix_attribution_touches_external_payment
    ON attribution_touches (empresa_id, provider, payment_id);
CREATE INDEX IF NOT EXISTS ix_attribution_touches_event_kind
    ON attribution_touches (empresa_id, event_kind, occurred_at);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_attribution_touches_empresa_message'
          AND conrelid = 'public.attribution_touches'::regclass
    ) THEN
        ALTER TABLE attribution_touches
            ADD CONSTRAINT fk_attribution_touches_empresa_message
            FOREIGN KEY (empresa_id, message_id)
            REFERENCES mensagens_historico(empresa_id, id);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS attribution_touch_resolutions (
    id uuid PRIMARY KEY,
    empresa_id uuid NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    touch_id uuid NOT NULL,
    resolution_status text NOT NULL DEFAULT 'unresolved',
    ad_id text NULL,
    adset_id text NULL,
    campaign_id text NULL,
    creative_id text NULL,
    ad_name_snapshot text NULL,
    adset_name_snapshot text NULL,
    campaign_name_snapshot text NULL,
    resolved_at timestamptz NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    CONSTRAINT uq_attribution_touch_resolutions_touch UNIQUE (empresa_id, touch_id),
    CONSTRAINT ck_attribution_touch_resolution_status CHECK (
        resolution_status IN ('resolved', 'partial', 'unresolved', 'not_applicable')
    ),
    CONSTRAINT fk_attribution_touch_resolutions_touch
        FOREIGN KEY (empresa_id, touch_id)
        REFERENCES attribution_touches(empresa_id, id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS ix_attribution_touch_resolutions_campaign
    ON attribution_touch_resolutions (empresa_id, campaign_id, adset_id, ad_id);

DO $$
BEGIN
    IF to_regclass('public.attribution_touches') IS NULL OR
       to_regclass('public.attribution_touch_resolutions') IS NULL THEN
        RAISE EXCEPTION 'postcheck failed: attribution ledger tables missing';
    END IF;
END $$;

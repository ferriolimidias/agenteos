DO $$
BEGIN
  IF to_regclass('public.attribution_touches') IS NULL
     OR to_regclass('public.attribution_touch_resolutions') IS NULL
     OR to_regclass('public.crm_tag_events') IS NULL
     OR to_regclass('public.meta_conversion_events') IS NULL
     OR to_regclass('public.business_messaging_conversion_mappings') IS NULL THEN
    RAISE EXCEPTION 'commercial intelligence requires P1A ledger, tag history and Meta conversion tables';
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='uq_meta_conversion_events_empresa_id_id' AND conrelid='public.meta_conversion_events'::regclass) THEN
    ALTER TABLE meta_conversion_events ADD CONSTRAINT uq_meta_conversion_events_empresa_id_id UNIQUE (empresa_id,id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname='uq_attribution_touch_resolutions_empresa_id_id' AND conrelid='public.attribution_touch_resolutions'::regclass) THEN
    ALTER TABLE attribution_touch_resolutions ADD CONSTRAINT uq_attribution_touch_resolutions_empresa_id_id UNIQUE (empresa_id,id);
  END IF;
END $$;

ALTER TABLE attribution_touch_resolutions
  ADD COLUMN IF NOT EXISTS last_attempt_at TIMESTAMPTZ NULL,
  ADD COLUMN IF NOT EXISTS error_code VARCHAR(80) NULL,
  ADD COLUMN IF NOT EXISTS api_version VARCHAR(24) NULL;

ALTER TABLE attribution_touch_resolutions DROP CONSTRAINT IF EXISTS ck_attribution_touch_resolution_status;
ALTER TABLE attribution_touch_resolutions
  ADD CONSTRAINT ck_attribution_touch_resolution_status
  CHECK (resolution_status IN ('resolved','partial','unresolved','not_applicable','not_found','forbidden','error_retryable'));

CREATE TABLE IF NOT EXISTS commercial_events (
  id UUID PRIMARY KEY,
  empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
  event_name VARCHAR(80) NOT NULL,
  event_category VARCHAR(40) NOT NULL,
  source_type VARCHAR(60) NOT NULL,
  source_id VARCHAR(200),
  provider VARCHAR(80),
  lead_id UUID,
  contact_id UUID,
  conversation_id UUID,
  message_id UUID,
  tag_event_id UUID,
  funnel_stage_id VARCHAR(120),
  checkout_id VARCHAR(200),
  payment_id VARCHAR(200),
  attribution_touch_id UUID,
  attribution_resolution_id UUID,
  attribution_status VARCHAR(24) NOT NULL DEFAULT 'unknown',
  attribution_model VARCHAR(24) NOT NULL DEFAULT 'last_touch',
  first_touch_id UUID,
  last_touch_id UUID,
  first_campaign_id VARCHAR(100),
  first_adset_id VARCHAR(100),
  first_ad_id VARCHAR(100),
  last_campaign_id VARCHAR(100),
  last_adset_id VARCHAR(100),
  last_ad_id VARCHAR(100),
  occurred_at TIMESTAMPTZ NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  value NUMERIC(18,6),
  currency VARCHAR(3),
  revenue_verified BOOLEAN NOT NULL DEFAULT FALSE,
  is_backfilled BOOLEAN NOT NULL DEFAULT FALSE,
  external_dispatch_policy VARCHAR(24) NOT NULL DEFAULT 'disabled',
  attribution_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  evidence_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  idempotency_key VARCHAR(128) NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_commercial_events_empresa_id_id UNIQUE (empresa_id,id),
  CONSTRAINT uq_commercial_events_idempotency UNIQUE (empresa_id,idempotency_key),
  CONSTRAINT fk_commercial_events_empresa_lead FOREIGN KEY (empresa_id,lead_id) REFERENCES crm_leads(empresa_id,id),
  CONSTRAINT fk_commercial_events_empresa_contact FOREIGN KEY (empresa_id,contact_id) REFERENCES channel_contacts(empresa_id,id),
  CONSTRAINT fk_commercial_events_empresa_conversation FOREIGN KEY (empresa_id,conversation_id) REFERENCES channel_conversations(empresa_id,id),
  CONSTRAINT fk_commercial_events_empresa_message FOREIGN KEY (empresa_id,message_id) REFERENCES mensagens_historico(empresa_id,id),
  CONSTRAINT fk_commercial_events_empresa_tag_event FOREIGN KEY (empresa_id,tag_event_id) REFERENCES crm_tag_events(empresa_id,id),
  CONSTRAINT fk_commercial_events_empresa_attribution_touch FOREIGN KEY (empresa_id,attribution_touch_id) REFERENCES attribution_touches(empresa_id,id),
  CONSTRAINT fk_commercial_events_empresa_attribution_resolution FOREIGN KEY (empresa_id,attribution_resolution_id) REFERENCES attribution_touch_resolutions(empresa_id,id),
  CONSTRAINT ck_commercial_events_name CHECK (event_name IN ('ViewContent','LeadSubmitted','QualifiedLead','AddToCart','InitiateCheckout','OrderCreated','CartAbandoned','Purchase','Refund','OrderShipped','OrderDelivered','OrderCanceled','OrderReturned','RatingProvided','ReviewProvided')),
  CONSTRAINT ck_commercial_events_dispatch_policy CHECK (external_dispatch_policy IN ('historical_no_send','disabled','not_applicable')),
  CONSTRAINT ck_commercial_events_backfill_no_send CHECK (NOT is_backfilled OR external_dispatch_policy='historical_no_send'),
  CONSTRAINT ck_commercial_events_value_currency CHECK ((value IS NULL AND currency IS NULL) OR (value IS NOT NULL AND currency IS NOT NULL AND char_length(currency)=3)),
  CONSTRAINT ck_commercial_events_json_objects CHECK (jsonb_typeof(evidence_json)='object' AND jsonb_typeof(metadata_json)='object' AND jsonb_typeof(attribution_snapshot)='object')
);

CREATE INDEX IF NOT EXISTS ix_commercial_events_tenant_time ON commercial_events(empresa_id,occurred_at);
CREATE INDEX IF NOT EXISTS ix_commercial_events_tenant_name_time ON commercial_events(empresa_id,event_name,occurred_at);
CREATE INDEX IF NOT EXISTS ix_commercial_events_lead_time ON commercial_events(empresa_id,lead_id,occurred_at);
CREATE INDEX IF NOT EXISTS ix_commercial_events_campaign_time ON commercial_events(empresa_id,last_campaign_id,occurred_at);
CREATE INDEX IF NOT EXISTS ix_commercial_events_payment ON commercial_events(empresa_id,provider,payment_id);

CREATE TABLE IF NOT EXISTS commercial_event_meta_links (
  id UUID PRIMARY KEY,
  empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
  meta_event_id UUID NOT NULL,
  commercial_event_id UUID,
  classification VARCHAR(24) NOT NULL,
  match_reason VARCHAR(80) NOT NULL,
  candidate_count INTEGER NOT NULL DEFAULT 0,
  reconciled_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_commercial_event_meta_link_legacy UNIQUE (empresa_id,meta_event_id),
  CONSTRAINT fk_commercial_event_meta_link_fact FOREIGN KEY (empresa_id,commercial_event_id) REFERENCES commercial_events(empresa_id,id),
  CONSTRAINT fk_commercial_event_meta_link_legacy FOREIGN KEY (empresa_id,meta_event_id) REFERENCES meta_conversion_events(empresa_id,id),
  CONSTRAINT ck_commercial_event_meta_link_classification CHECK (classification IN ('LINKED_EXACT','LINKED_PROBABLE','AMBIGUOUS','UNLINKED')),
  CONSTRAINT ck_commercial_event_meta_link_exact_reference CHECK (classification <> 'LINKED_EXACT' OR commercial_event_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_commercial_event_meta_link_class ON commercial_event_meta_links(empresa_id,classification);

CREATE TABLE IF NOT EXISTS meta_ads_insights_daily (
  id UUID PRIMARY KEY,
  empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
  ad_account_id VARCHAR(100) NOT NULL,
  account_currency VARCHAR(3),
  entity_level VARCHAR(16) NOT NULL,
  entity_id VARCHAR(100) NOT NULL,
  account_name VARCHAR(240),
  campaign_id VARCHAR(100),
  campaign_name VARCHAR(240),
  adset_id VARCHAR(100),
  adset_name VARCHAR(240),
  ad_id VARCHAR(100),
  ad_name VARCHAR(240),
  date_start DATE NOT NULL,
  date_stop DATE NOT NULL,
  attribution_setting VARCHAR(120) NOT NULL DEFAULT 'unspecified',
  spend NUMERIC(18,6),
  impressions BIGINT,
  reach BIGINT,
  frequency NUMERIC(18,8),
  clicks BIGINT,
  ctr NUMERIC(18,8),
  cpc NUMERIC(18,8),
  cpm NUMERIC(18,8),
  messaging_conversations NUMERIC(18,6),
  actions_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  action_values_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  raw_metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  graph_api_version VARCHAR(24) NOT NULL,
  fetched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_meta_ads_insights_daily_key UNIQUE (empresa_id,ad_account_id,entity_level,entity_id,date_start,date_stop,attribution_setting),
  CONSTRAINT ck_meta_ads_insights_daily_level CHECK (entity_level IN ('account','campaign','adset','ad')),
  CONSTRAINT ck_meta_ads_insights_daily_date CHECK (date_stop >= date_start),
  CONSTRAINT ck_meta_ads_insights_daily_json CHECK (jsonb_typeof(actions_json)='array' AND jsonb_typeof(action_values_json)='array' AND jsonb_typeof(raw_metrics_json)='object')
);
CREATE INDEX IF NOT EXISTS ix_meta_ads_insights_daily_campaign_date ON meta_ads_insights_daily(empresa_id,campaign_id,date_start);
CREATE INDEX IF NOT EXISTS ix_meta_ads_insights_daily_adset_date ON meta_ads_insights_daily(empresa_id,adset_id,date_start);
CREATE INDEX IF NOT EXISTS ix_meta_ads_insights_daily_ad_date ON meta_ads_insights_daily(empresa_id,ad_id,date_start);
ALTER TABLE meta_ads_insights_daily ADD COLUMN IF NOT EXISTS account_currency VARCHAR(3);

DO $$
BEGIN
  IF to_regclass('public.commercial_events') IS NULL
     OR to_regclass('public.commercial_event_meta_links') IS NULL
     OR to_regclass('public.meta_ads_insights_daily') IS NULL THEN
    RAISE EXCEPTION 'postcheck failed: commercial intelligence tables missing';
  END IF;
END $$;

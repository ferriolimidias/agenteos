DO $$
BEGIN
  IF to_regclass('public.commercial_events') IS NULL
     OR to_regclass('public.commercial_meta_conversion_projections') IS NULL
     OR to_regclass('public.meta_conversion_events') IS NULL
     OR to_regclass('public.commercial_event_meta_links') IS NULL THEN
    RAISE EXCEPTION 'canonical conversion outbox requires commercial facts, shadow projections and legacy reconciliation';
  END IF;
END $$;

DO $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_commercial_meta_projection_empresa_id_id') THEN
    ALTER TABLE commercial_meta_conversion_projections
      ADD CONSTRAINT uq_commercial_meta_projection_empresa_id_id UNIQUE (empresa_id,id);
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS canonical_conversion_outbox (
  id UUID PRIMARY KEY,
  empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
  commercial_event_id UUID NOT NULL,
  projection_id UUID NOT NULL,
  provider VARCHAR(32) NOT NULL DEFAULT 'meta',
  destination_type VARCHAR(48) NOT NULL DEFAULT 'business_messaging_dataset',
  dataset_id_masked VARCHAR(32),
  waba_id_masked VARCHAR(32),
  event_name VARCHAR(80) NOT NULL,
  event_id VARCHAR(64) NOT NULL,
  idempotency_key VARCHAR(128) NOT NULL,
  payload_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb,
  payload_version INTEGER NOT NULL DEFAULT 1,
  status VARCHAR(32) NOT NULL,
  dispatch_mode VARCHAR(16) NOT NULL DEFAULT 'shadow',
  historical_no_send BOOLEAN NOT NULL DEFAULT FALSE,
  eligible_at TIMESTAMPTZ,
  next_attempt_at TIMESTAMPTZ,
  attempt_count INTEGER NOT NULL DEFAULT 0,
  last_attempt_at TIMESTAMPTZ,
  lease_owner VARCHAR(120),
  lease_expires_at TIMESTAMPTZ,
  last_error_class VARCHAR(80),
  last_error_code VARCHAR(100),
  delivered_at TIMESTAMPTZ,
  provider_response_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_canonical_conversion_outbox_empresa_id UNIQUE (empresa_id,id),
  CONSTRAINT uq_canonical_conversion_outbox_idempotency UNIQUE (empresa_id,idempotency_key),
  CONSTRAINT uq_canonical_conversion_outbox_event UNIQUE (empresa_id,commercial_event_id),
  CONSTRAINT fk_conversion_outbox_fact FOREIGN KEY (empresa_id,commercial_event_id)
    REFERENCES commercial_events(empresa_id,id) ON DELETE CASCADE,
  CONSTRAINT fk_conversion_outbox_projection FOREIGN KEY (empresa_id,projection_id)
    REFERENCES commercial_meta_conversion_projections(empresa_id,id) ON DELETE CASCADE,
  CONSTRAINT ck_conversion_outbox_provider CHECK (provider='meta'),
  CONSTRAINT ck_conversion_outbox_destination CHECK (destination_type='business_messaging_dataset'),
  CONSTRAINT ck_conversion_outbox_status CHECK (status IN (
    'SHADOW','BLOCKED','HISTORICAL_BLOCKED','READY','CLAIMED','RETRY',
    'DELIVERED','FAILED_PERMANENT','UNKNOWN_DELIVERY'
  )),
  CONSTRAINT ck_conversion_outbox_mode CHECK (dispatch_mode IN ('disabled','shadow','live')),
  CONSTRAINT ck_conversion_outbox_live_stays_blocked CHECK (dispatch_mode <> 'live' OR status='BLOCKED'),
  CONSTRAINT ck_conversion_outbox_history_no_send CHECK (
    (historical_no_send AND status='HISTORICAL_BLOCKED') OR
    (NOT historical_no_send AND status<>'HISTORICAL_BLOCKED')
  ),
  CONSTRAINT ck_conversion_outbox_attempt_count CHECK (attempt_count >= 0),
  CONSTRAINT ck_conversion_outbox_payload_object CHECK (
    jsonb_typeof(payload_snapshot)='object' AND jsonb_typeof(provider_response_metadata)='object'
  )
);

CREATE INDEX IF NOT EXISTS ix_conversion_outbox_claim
  ON canonical_conversion_outbox(dispatch_mode,status,next_attempt_at,lease_expires_at,created_at);
CREATE INDEX IF NOT EXISTS ix_conversion_outbox_tenant_status
  ON canonical_conversion_outbox(empresa_id,status,created_at);

CREATE TABLE IF NOT EXISTS canonical_conversion_delivery_attempts (
  id UUID PRIMARY KEY,
  empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
  outbox_id UUID NOT NULL,
  attempt_number INTEGER NOT NULL,
  attempt_kind VARCHAR(32) NOT NULL,
  started_at TIMESTAMPTZ NOT NULL,
  finished_at TIMESTAMPTZ,
  status VARCHAR(32) NOT NULL,
  error_class VARCHAR(80),
  provider_ack_status VARCHAR(32) NOT NULL DEFAULT 'NOT_ATTEMPTED',
  request_correlation_id VARCHAR(80),
  response_metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT fk_conversion_attempt_outbox FOREIGN KEY (empresa_id,outbox_id)
    REFERENCES canonical_conversion_outbox(empresa_id,id) ON DELETE CASCADE,
  CONSTRAINT uq_conversion_attempt_number UNIQUE (empresa_id,outbox_id,attempt_number),
  CONSTRAINT ck_conversion_attempt_number CHECK (attempt_number > 0),
  CONSTRAINT ck_conversion_attempt_kind CHECK (attempt_kind IN ('SHADOW_SIMULATION','EXTERNAL')),
  CONSTRAINT ck_conversion_attempt_status CHECK (status IN (
    'SIMULATED','FAILED_PRE_SEND','RETRY','DELIVERED','FAILED_PERMANENT','UNKNOWN_DELIVERY'
  )),
  CONSTRAINT ck_conversion_attempt_ack CHECK (provider_ack_status IN ('NOT_ATTEMPTED','ACKED','REJECTED','UNKNOWN')),
  CONSTRAINT ck_conversion_shadow_never_acked CHECK (
    attempt_kind <> 'SHADOW_SIMULATION' OR provider_ack_status='NOT_ATTEMPTED'
  ),
  CONSTRAINT ck_conversion_attempt_response_object CHECK (jsonb_typeof(response_metadata)='object')
);
CREATE INDEX IF NOT EXISTS ix_conversion_attempt_tenant_outbox
  ON canonical_conversion_delivery_attempts(empresa_id,outbox_id,attempt_number);

CREATE OR REPLACE FUNCTION prevent_conversion_outbox_history_release()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  fact_is_historical BOOLEAN := FALSE;
BEGIN
  SELECT (e.is_backfilled OR e.external_dispatch_policy='historical_no_send')
    INTO fact_is_historical
    FROM commercial_events e
    WHERE e.empresa_id=NEW.empresa_id AND e.id=NEW.commercial_event_id;
  IF COALESCE(fact_is_historical,FALSE) AND NOT NEW.historical_no_send THEN
    RAISE EXCEPTION 'historical commercial fact cannot create a deliverable outbox intent';
  END IF;
  IF NEW.historical_no_send AND NEW.status <> 'HISTORICAL_BLOCKED' THEN
    RAISE EXCEPTION 'historical outbox intent must remain blocked';
  END IF;
  IF TG_OP='UPDATE' AND OLD.historical_no_send AND NOT NEW.historical_no_send THEN
    RAISE EXCEPTION 'historical_no_send is immutable';
  END IF;
  IF TG_OP='UPDATE' AND OLD.historical_no_send AND NEW.status <> 'HISTORICAL_BLOCKED' THEN
    RAISE EXCEPTION 'historical outbox intent cannot be released';
  END IF;
  IF NEW.dispatch_mode='live' AND NEW.status <> 'BLOCKED' THEN
    RAISE EXCEPTION 'canonical live dispatch is not enabled';
  END IF;
  RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_conversion_outbox_history_no_send ON canonical_conversion_outbox;
CREATE TRIGGER trg_conversion_outbox_history_no_send
  BEFORE INSERT OR UPDATE ON canonical_conversion_outbox
  FOR EACH ROW EXECUTE FUNCTION prevent_conversion_outbox_history_release();

-- Seed durable audit intents from the already persisted shadow projections.
-- Existing historical facts can only become HISTORICAL_BLOCKED and are never READY.
INSERT INTO canonical_conversion_outbox (
  id,empresa_id,commercial_event_id,projection_id,provider,destination_type,
  dataset_id_masked,waba_id_masked,event_name,event_id,idempotency_key,
  payload_snapshot,payload_version,status,dispatch_mode,historical_no_send,eligible_at
)
SELECT
  gen_random_uuid(),p.empresa_id,p.commercial_event_id,p.id,'meta','business_messaging_dataset',
  p.dataset_id_masked,p.waba_id_masked,p.event_name,p.canonical_event_id,
  'canonical:'||p.canonical_event_id,
  jsonb_build_object(
    'event_name',p.event_name,'event_id',p.canonical_event_id,'event_time',p.event_time,
    'action_source',p.action_source,'payload_hash',p.payload_hash,
    'dataset_id_masked',p.dataset_id_masked,'waba_id_masked',p.waba_id_masked,
    'ctwa_present',p.ctwa_present,'value',p.value,'currency',p.currency,
    'projection_status',p.projection_status,'historical_no_send',
    (e.is_backfilled OR e.external_dispatch_policy='historical_no_send')
  ),
  1,
  CASE
    WHEN e.is_backfilled OR e.external_dispatch_policy='historical_no_send' THEN 'HISTORICAL_BLOCKED'
    WHEN p.projection_status='valid' AND p.mode='shadow'
      AND p.legacy_reference_classification='UNLINKED' THEN 'SHADOW'
    ELSE 'BLOCKED'
  END,
  CASE WHEN p.mode='disabled' THEN 'disabled' ELSE 'shadow' END,
  (e.is_backfilled OR e.external_dispatch_policy='historical_no_send'),
  p.last_projected_at
FROM commercial_meta_conversion_projections p
JOIN commercial_events e ON e.empresa_id=p.empresa_id AND e.id=p.commercial_event_id
ON CONFLICT (empresa_id,commercial_event_id) DO NOTHING;

DO $$
BEGIN
  IF to_regclass('public.canonical_conversion_outbox') IS NULL
     OR to_regclass('public.canonical_conversion_delivery_attempts') IS NULL THEN
    RAISE EXCEPTION 'postcheck failed: canonical conversion outbox schema missing';
  END IF;
END $$;

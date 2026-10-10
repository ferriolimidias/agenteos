DO $$
BEGIN
  IF to_regclass('public.commercial_events') IS NULL
     OR to_regclass('public.meta_conversion_events') IS NULL
     OR to_regclass('public.business_messaging_conversion_mappings') IS NULL THEN
    RAISE EXCEPTION 'canonical Meta shadow requires P1B commercial facts and Business Messaging mapping contract';
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS commercial_meta_event_policies (
  id UUID PRIMARY KEY,
  empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
  event_name VARCHAR(80) NOT NULL,
  mode VARCHAR(16) NOT NULL DEFAULT 'disabled',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_commercial_meta_policy_tenant_event UNIQUE (empresa_id,event_name),
  CONSTRAINT ck_commercial_meta_policy_mode CHECK (mode IN ('disabled','shadow','live'))
);
CREATE INDEX IF NOT EXISTS ix_commercial_meta_policy_mode
  ON commercial_meta_event_policies(empresa_id,mode);

-- SHADOW has no external side effects. Seed only the supported event taxonomy;
-- code enforces a hard block for LIVE until a separately approved cutover.
INSERT INTO commercial_meta_event_policies (id,empresa_id,event_name,mode)
SELECT gen_random_uuid(),e.id,event_name,'shadow'
FROM empresas e
CROSS JOIN unnest(ARRAY[
  'ViewContent','LeadSubmitted','QualifiedLead','AddToCart',
  'InitiateCheckout','OrderCreated','CartAbandoned','Purchase'
]::varchar[]) AS event_names(event_name)
ON CONFLICT (empresa_id,event_name) DO NOTHING;

CREATE TABLE IF NOT EXISTS commercial_meta_conversion_projections (
  id UUID PRIMARY KEY,
  empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
  commercial_event_id UUID NOT NULL,
  legacy_event_id UUID,
  mapping_id UUID,
  projection_version INTEGER NOT NULL DEFAULT 1,
  canonical_event_id VARCHAR(64) NOT NULL,
  event_name VARCHAR(80) NOT NULL,
  mode VARCHAR(16) NOT NULL DEFAULT 'shadow',
  projection_status VARCHAR(24) NOT NULL,
  comparison_classification VARCHAR(40) NOT NULL DEFAULT 'NO_LEGACY_REFERENCE',
  legacy_reference_classification VARCHAR(24) NOT NULL DEFAULT 'UNLINKED',
  delivery_state VARCHAR(40) NOT NULL DEFAULT 'SHADOW_ONLY',
  event_time BIGINT,
  action_source VARCHAR(40),
  dataset_id_masked VARCHAR(32),
  waba_id_masked VARCHAR(32),
  ctwa_present BOOLEAN NOT NULL DEFAULT FALSE,
  value NUMERIC(18,6),
  currency VARCHAR(3),
  payload_hash VARCHAR(64),
  reason_code VARCHAR(80),
  legacy_candidate_count INTEGER NOT NULL DEFAULT 0,
  comparison_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  safe_summary_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  last_projected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_commercial_meta_projection_event_version UNIQUE (empresa_id,commercial_event_id,projection_version),
  CONSTRAINT uq_commercial_meta_projection_canonical_id UNIQUE (empresa_id,canonical_event_id),
  CONSTRAINT fk_commercial_meta_projection_fact
    FOREIGN KEY (empresa_id,commercial_event_id)
    REFERENCES commercial_events(empresa_id,id) ON DELETE CASCADE,
  CONSTRAINT fk_commercial_meta_projection_exact_legacy
    FOREIGN KEY (empresa_id,legacy_event_id)
    REFERENCES meta_conversion_events(empresa_id,id),
  CONSTRAINT fk_commercial_meta_projection_mapping
    FOREIGN KEY (mapping_id)
    REFERENCES business_messaging_conversion_mappings(id),
  CONSTRAINT ck_commercial_meta_projection_mode CHECK (mode IN ('disabled','shadow','live')),
  CONSTRAINT ck_commercial_meta_projection_status CHECK (projection_status IN ('disabled','valid','invalid','unsupported','live_blocked')),
  CONSTRAINT ck_commercial_meta_projection_comparison CHECK (comparison_classification IN (
    'EXACT','SEMANTICALLY_EQUIVALENT','MISSING_ATTRIBUTION','MISSING_REQUIRED_FIELDS',
    'EVENT_ID_MISMATCH','VALUE_MISMATCH','UNSUPPORTED_EVENT','NO_LEGACY_REFERENCE',
    'EVENT_NAME_MISMATCH','CONTEXT_MISMATCH'
  )),
  CONSTRAINT ck_commercial_meta_projection_legacy_class CHECK (legacy_reference_classification IN ('EXACT_LINKED','PROBABLE_ONLY','AMBIGUOUS','UNLINKED')),
  CONSTRAINT ck_commercial_meta_projection_delivery_state CHECK (delivery_state IN (
    'NOT_SENT','SHADOW_ONLY','ALREADY_DELIVERED_BY_LEGACY','LEGACY_ACCEPTED','LEGACY_FAILED',
    'UNKNOWN_DELIVERY','NOT_APPLICABLE','BLOCKED'
  )),
  CONSTRAINT ck_commercial_meta_projection_json_objects CHECK (
    jsonb_typeof(comparison_json)='object' AND jsonb_typeof(safe_summary_json)='object'
  ),
  CONSTRAINT ck_commercial_meta_projection_canonical_id_length CHECK (length(canonical_event_id)=64)
);
CREATE INDEX IF NOT EXISTS ix_commercial_meta_projection_tenant_status
  ON commercial_meta_conversion_projections(empresa_id,projection_status);
CREATE INDEX IF NOT EXISTS ix_commercial_meta_projection_tenant_delivery
  ON commercial_meta_conversion_projections(empresa_id,delivery_state);

DO $$
BEGIN
  IF to_regclass('public.commercial_meta_event_policies') IS NULL
     OR to_regclass('public.commercial_meta_conversion_projections') IS NULL THEN
    RAISE EXCEPTION 'postcheck failed: Meta conversion shadow schema missing';
  END IF;
END $$;

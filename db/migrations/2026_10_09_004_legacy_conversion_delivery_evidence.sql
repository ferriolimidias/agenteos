-- Additive telemetry only: never enables transport, replay, or legacy blocking.
CREATE TABLE IF NOT EXISTS legacy_conversion_delivery_evidence (
 id UUID PRIMARY KEY,
 empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
 commercial_event_id UUID,
 legacy_event_id UUID,
 external_event_id_hash VARCHAR(64) NOT NULL,
 external_event_id_safe VARCHAR(64),
 event_name VARCHAR(80) NOT NULL,
 dataset_id_hash VARCHAR(64) NOT NULL,
 waba_id_hash VARCHAR(64),
 source_type VARCHAR(60) NOT NULL,
 source_id_hash VARCHAR(64),
 transport_path VARCHAR(120) NOT NULL,
 identity_classification VARCHAR(24) NOT NULL DEFAULT 'UNLINKED',
 request_correlation_id UUID NOT NULL,
 attempt_started_at TIMESTAMPTZ NOT NULL,
 attempt_finished_at TIMESTAMPTZ,
 request_outcome VARCHAR(32) NOT NULL DEFAULT 'LOCAL_PREPARED',
 http_status INTEGER,
 provider_response JSONB NOT NULL DEFAULT '{}'::jsonb,
 delivery_uncertainty BOOLEAN NOT NULL DEFAULT FALSE,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT fk_legacy_delivery_evidence_fact FOREIGN KEY (empresa_id,commercial_event_id)
 REFERENCES commercial_events(empresa_id,id),
 CONSTRAINT fk_legacy_delivery_evidence_legacy FOREIGN KEY (empresa_id,legacy_event_id)
 REFERENCES meta_conversion_events(empresa_id,id),
 CONSTRAINT uq_legacy_delivery_evidence_request_event UNIQUE (request_correlation_id,external_event_id_hash),
 CONSTRAINT ck_legacy_delivery_evidence_outcome CHECK (request_outcome IN
 ('LOCAL_PREPARED','REQUEST_ATTEMPTED','PROVIDER_ACCEPTED','PROVIDER_REJECTED','DELIVERY_UNKNOWN','NOT_SENT','BLOCKED')),
 CONSTRAINT ck_legacy_delivery_evidence_identity CHECK (identity_classification IN ('EXACT_LINKED','UNLINKED')),
 CONSTRAINT ck_legacy_delivery_evidence_exact CHECK (identity_classification <> 'EXACT_LINKED' OR (commercial_event_id IS NOT NULL AND legacy_event_id IS NOT NULL)),
 CONSTRAINT ck_legacy_delivery_evidence_http CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
 CONSTRAINT ck_legacy_delivery_evidence_response CHECK (jsonb_typeof(provider_response)='object')
);
CREATE INDEX IF NOT EXISTS ix_legacy_delivery_evidence_tenant_time
 ON legacy_conversion_delivery_evidence(empresa_id,attempt_started_at);
CREATE INDEX IF NOT EXISTS ix_legacy_delivery_evidence_identity
 ON legacy_conversion_delivery_evidence(empresa_id,external_event_id_hash,dataset_id_hash,waba_id_hash);
CREATE TABLE IF NOT EXISTS conversion_delivery_identities (
 id UUID PRIMARY KEY,
 empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
 commercial_event_id UUID NOT NULL,
 identity_key VARCHAR(64) NOT NULL,
 external_event_id_hash VARCHAR(64) NOT NULL,
 owner VARCHAR(16) NOT NULL,
 ownership_mode VARCHAR(24) NOT NULL DEFAULT 'SHADOW_VALIDATION',
 delivery_state VARCHAR(24) NOT NULL DEFAULT 'RESERVED',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT fk_conversion_shared_identity_fact FOREIGN KEY (empresa_id,commercial_event_id)
 REFERENCES commercial_events(empresa_id,id),
 CONSTRAINT uq_conversion_shared_identity UNIQUE (empresa_id,identity_key),
 CONSTRAINT ck_conversion_shared_identity_owner CHECK (owner IN ('legacy','canonical')),
 CONSTRAINT ck_conversion_shared_identity_shadow CHECK (ownership_mode='SHADOW_VALIDATION'),
 CONSTRAINT ck_conversion_shared_identity_state CHECK (delivery_state IN ('RESERVED','PROVIDER_ACCEPTED','DELIVERY_UNKNOWN'))
);
ALTER TABLE conversion_delivery_identities ALTER COLUMN ownership_mode TYPE VARCHAR(24);
CREATE OR REPLACE FUNCTION protect_conversion_delivery_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
  IF OLD.delivery_state IN ('PROVIDER_ACCEPTED','DELIVERY_UNKNOWN') THEN
   RAISE EXCEPTION 'accepted or uncertain delivery ownership cannot be deleted';
  END IF;
  RETURN OLD;
 END IF;
 IF OLD.identity_key<>NEW.identity_key OR OLD.external_event_id_hash<>NEW.external_event_id_hash
 OR OLD.owner<>NEW.owner OR OLD.commercial_event_id<>NEW.commercial_event_id OR OLD.empresa_id<>NEW.empresa_id THEN
  RAISE EXCEPTION 'shared external delivery identity ownership is immutable';
 END IF;
 IF OLD.delivery_state IN ('PROVIDER_ACCEPTED','DELIVERY_UNKNOWN') AND NEW.delivery_state<>OLD.delivery_state THEN
  RAISE EXCEPTION 'accepted or uncertain delivery cannot be silently released';
 END IF;
 RETURN NEW;
END $$;
DROP TRIGGER IF EXISTS trg_conversion_delivery_identity_immutable ON conversion_delivery_identities;
CREATE TRIGGER trg_conversion_delivery_identity_immutable BEFORE UPDATE OR DELETE ON conversion_delivery_identities
 FOR EACH ROW EXECUTE FUNCTION protect_conversion_delivery_identity();

CREATE TABLE IF NOT EXISTS leoft_catalog_entries (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  catalog_id text NOT NULL UNIQUE,
  canonical_name text NOT NULL,
  normalized_name text NOT NULL,
  muscle_group text,
  equipment text,
  body_position text,
  variation text,
  laterality text,
  grip text,
  approval_status text NOT NULL,
  confidence numeric,
  source text,
  source_metadata jsonb,
  search_document tsvector,
  created_at timestamp NOT NULL DEFAULT now(),
  updated_at timestamp NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS leoft_catalog_aliases (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  catalog_entry_id uuid NOT NULL REFERENCES leoft_catalog_entries(id) ON DELETE CASCADE,
  alias text NOT NULL,
  normalized_alias text NOT NULL,
  created_at timestamp NOT NULL DEFAULT now(),
  CONSTRAINT uq_leoft_catalog_alias_entry_normalized UNIQUE (catalog_entry_id, normalized_alias)
);

CREATE TABLE IF NOT EXISTS leoft_catalog_media (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  media_id text NOT NULL UNIQUE,
  catalog_entry_id uuid NOT NULL REFERENCES leoft_catalog_entries(id) ON DELETE CASCADE,
  media_type text,
  format text,
  mime_type text,
  storage_provider text,
  storage_path text NOT NULL,
  thumbnail_path text,
  sha256 text NOT NULL,
  file_size bigint,
  width integer,
  height integer,
  duration_ms integer,
  metadata jsonb,
  created_at timestamp NOT NULL DEFAULT now(),
  updated_at timestamp NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS leoft_catalog_signatures (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  catalog_entry_id uuid NOT NULL REFERENCES leoft_catalog_entries(id) ON DELETE CASCADE,
  signature_key text NOT NULL,
  signature_version text NOT NULL,
  normalized_name text,
  muscle_group text,
  equipment text,
  body_position text,
  variation text,
  laterality text,
  grip text,
  signature_payload jsonb NOT NULL,
  created_at timestamp NOT NULL DEFAULT now(),
  CONSTRAINT uq_leoft_catalog_signature_entry_version UNIQUE (catalog_entry_id, signature_version)
);

CREATE TABLE IF NOT EXISTS leoft_catalog_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  version text NOT NULL UNIQUE,
  source_hash text NOT NULL,
  record_count integer,
  media_count integer,
  alias_count integer,
  signature_count integer,
  status text,
  metadata jsonb,
  created_at timestamp NOT NULL DEFAULT now(),
  activated_at timestamp
);

CREATE TABLE IF NOT EXISTS leoft_catalog_import_runs (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  idempotency_key text NOT NULL UNIQUE,
  catalog_version_id uuid REFERENCES leoft_catalog_versions(id) ON DELETE SET NULL,
  source_filename text,
  source_sha256 text,
  status text,
  counters jsonb,
  error_summary text,
  started_at timestamp NOT NULL DEFAULT now(),
  finished_at timestamp
);

CREATE TABLE IF NOT EXISTS leoft_media_approvals (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  signature_key text NOT NULL,
  signature_version text NOT NULL,
  catalog_entry_id uuid REFERENCES leoft_catalog_entries(id) ON DELETE SET NULL,
  media_id uuid REFERENCES leoft_catalog_media(id) ON DELETE SET NULL,
  approval_method text NOT NULL,
  approved_by text NOT NULL,
  confidence numeric,
  revision_number integer NOT NULL,
  supersedes_approval_id uuid REFERENCES leoft_media_approvals(id) ON DELETE SET NULL,
  idempotency_key text NOT NULL UNIQUE,
  active boolean NOT NULL,
  source_plan_id uuid REFERENCES leoft_training_plans(id) ON DELETE SET NULL,
  source_workout_id text,
  source_exercise_id uuid REFERENCES leoft_training_exercises(id) ON DELETE SET NULL,
  notes text,
  created_at timestamp NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS leoft_media_candidate_rejections (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  signature_key text NOT NULL,
  signature_version text NOT NULL,
  catalog_entry_id uuid REFERENCES leoft_catalog_entries(id) ON DELETE SET NULL,
  media_id uuid REFERENCES leoft_catalog_media(id) ON DELETE SET NULL,
  rejected_by text NOT NULL,
  reason text,
  idempotency_key text NOT NULL UNIQUE,
  active boolean NOT NULL,
  created_at timestamp NOT NULL DEFAULT now(),
  restored_at timestamp
);

CREATE TABLE IF NOT EXISTS leoft_approval_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_type text NOT NULL,
  signature_key text,
  approval_id uuid REFERENCES leoft_media_approvals(id) ON DELETE SET NULL,
  previous_approval_id uuid REFERENCES leoft_media_approvals(id) ON DELETE SET NULL,
  catalog_entry_id uuid REFERENCES leoft_catalog_entries(id) ON DELETE SET NULL,
  media_id uuid REFERENCES leoft_catalog_media(id) ON DELETE SET NULL,
  actor_identifier text,
  source text,
  reason text,
  payload jsonb NOT NULL,
  idempotency_key text NOT NULL UNIQUE,
  event_version text NOT NULL,
  created_at timestamp NOT NULL DEFAULT now()
);

CREATE OR REPLACE FUNCTION leoft_catalog_entries_search_document_update()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  NEW.search_document := to_tsvector(
    'simple',
    concat_ws(' ', NEW.canonical_name, NEW.normalized_name, NEW.muscle_group,
      NEW.equipment, NEW.body_position, NEW.variation, NEW.laterality, NEW.grip)
  );
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_leoft_catalog_entries_search_document ON leoft_catalog_entries;
CREATE TRIGGER trg_leoft_catalog_entries_search_document
BEFORE INSERT OR UPDATE ON leoft_catalog_entries
FOR EACH ROW EXECUTE FUNCTION leoft_catalog_entries_search_document_update();

CREATE OR REPLACE FUNCTION leoft_approval_events_append_only()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'leoft_approval_events is append-only';
END;
$$;

DROP TRIGGER IF EXISTS trg_leoft_approval_events_append_only ON leoft_approval_events;
CREATE TRIGGER trg_leoft_approval_events_append_only
BEFORE UPDATE OR DELETE ON leoft_approval_events
FOR EACH ROW EXECUTE FUNCTION leoft_approval_events_append_only();

CREATE UNIQUE INDEX IF NOT EXISTS ux_leoft_media_approvals_active_signature
  ON leoft_media_approvals (signature_key, signature_version) WHERE active;
CREATE INDEX IF NOT EXISTS ix_leoft_catalog_entries_normalized_name ON leoft_catalog_entries (normalized_name);
CREATE INDEX IF NOT EXISTS ix_leoft_catalog_entries_muscle_group ON leoft_catalog_entries (muscle_group);
CREATE INDEX IF NOT EXISTS ix_leoft_catalog_entries_equipment ON leoft_catalog_entries (equipment);
CREATE INDEX IF NOT EXISTS ix_leoft_catalog_entries_body_position ON leoft_catalog_entries (body_position);
CREATE INDEX IF NOT EXISTS ix_leoft_catalog_entries_variation ON leoft_catalog_entries (variation);
CREATE INDEX IF NOT EXISTS ix_leoft_catalog_entries_laterality ON leoft_catalog_entries (laterality);
CREATE INDEX IF NOT EXISTS ix_leoft_catalog_entries_grip ON leoft_catalog_entries (grip);
CREATE INDEX IF NOT EXISTS ix_leoft_catalog_entries_approval_status ON leoft_catalog_entries (approval_status);
CREATE INDEX IF NOT EXISTS ix_leoft_catalog_entries_search_document ON leoft_catalog_entries USING gin (search_document);
CREATE INDEX IF NOT EXISTS ix_leoft_catalog_aliases_normalized_alias ON leoft_catalog_aliases (normalized_alias);
CREATE INDEX IF NOT EXISTS ix_leoft_catalog_aliases_entry ON leoft_catalog_aliases (catalog_entry_id);
CREATE INDEX IF NOT EXISTS ix_leoft_catalog_media_entry ON leoft_catalog_media (catalog_entry_id);
CREATE INDEX IF NOT EXISTS ix_leoft_catalog_signatures_key_version ON leoft_catalog_signatures (signature_key, signature_version);
CREATE INDEX IF NOT EXISTS ix_leoft_catalog_import_runs_source ON leoft_catalog_import_runs (source_sha256);
CREATE INDEX IF NOT EXISTS ix_leoft_media_approvals_signature ON leoft_media_approvals (signature_key, signature_version);
CREATE INDEX IF NOT EXISTS ix_leoft_approval_events_signature_created ON leoft_approval_events (signature_key, created_at);

BEGIN;

-- Additive schema-v2 fields. Legacy identifiers and zero-based position columns remain intact.
ALTER TABLE leoft_training_plans
  ADD COLUMN IF NOT EXISTS product_id text,
  ADD COLUMN IF NOT EXISTS generation_job_id uuid,
  ADD COLUMN IF NOT EXISTS current_version_id uuid;

ALTER TABLE leoft_training_plan_versions
  ADD COLUMN IF NOT EXISTS source_snapshot_id uuid,
  ADD COLUMN IF NOT EXISTS blueprint jsonb,
  ADD COLUMN IF NOT EXISTS content_hash text,
  ADD COLUMN IF NOT EXISTS generation_completed_at timestamptz,
  ADD COLUMN IF NOT EXISTS submitted_for_review_at timestamptz,
  ADD COLUMN IF NOT EXISTS approved_at timestamptz,
  ADD COLUMN IF NOT EXISTS published_at timestamptz,
  ADD COLUMN IF NOT EXISTS approved_by text,
  ADD COLUMN IF NOT EXISTS updated_at timestamptz NOT NULL DEFAULT now();

ALTER TABLE leoft_training_weeks
  ADD COLUMN IF NOT EXISTS phase_number integer,
  ADD COLUMN IF NOT EXISTS objective text,
  ADD COLUMN IF NOT EXISTS progression_notes text,
  ADD COLUMN IF NOT EXISTS status text;

ALTER TABLE leoft_training_sessions
  ADD COLUMN IF NOT EXISTS session_task_id uuid,
  ADD COLUMN IF NOT EXISTS session_code text,
  ADD COLUMN IF NOT EXISTS objective text,
  ADD COLUMN IF NOT EXISTS sequence integer,
  ADD COLUMN IF NOT EXISTS estimated_duration_minutes integer,
  ADD COLUMN IF NOT EXISTS status text;

ALTER TABLE leoft_training_exercises
  ADD COLUMN IF NOT EXISTS catalog_id text,
  ADD COLUMN IF NOT EXISTS media_id text,
  ADD COLUMN IF NOT EXISTS catalog_version text,
  ADD COLUMN IF NOT EXISTS "order" integer,
  ADD COLUMN IF NOT EXISTS repetitions text,
  ADD COLUMN IF NOT EXISTS duration_seconds integer,
  ADD COLUMN IF NOT EXISTS rest_seconds integer,
  ADD COLUMN IF NOT EXISTS tempo text,
  ADD COLUMN IF NOT EXISTS load_guidance text,
  ADD COLUMN IF NOT EXISTS notes text,
  ADD COLUMN IF NOT EXISTS progression_rule jsonb,
  ADD COLUMN IF NOT EXISTS substitutions jsonb,
  ADD COLUMN IF NOT EXISTS prescription_mode text,
  ADD COLUMN IF NOT EXISTS canonical_name text,
  ADD COLUMN IF NOT EXISTS selection_reason text,
  ADD COLUMN IF NOT EXISTS association_validated_at timestamptz;

-- Replace the legacy-only status check with a union that keeps every old value valid.
ALTER TABLE leoft_training_plans DROP CONSTRAINT IF EXISTS ck_leoft_training_plans_status;
ALTER TABLE leoft_training_plans ADD CONSTRAINT ck_leoft_training_plans_status CHECK (status IN (
  'pending_approval','approved','rejected','revision_requested',
  'draft','generating','awaiting_review','published','cancelled'
)) NOT VALID;

CREATE TABLE IF NOT EXISTS leoft_payment_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id uuid NOT NULL,
  event_type text NOT NULL,
  occurred_at timestamptz NOT NULL,
  received_at timestamptz NOT NULL DEFAULT now(),
  company_id uuid NOT NULL REFERENCES empresas(id) ON DELETE RESTRICT,
  contact_id uuid NOT NULL,
  student_id uuid NOT NULL,
  product_id text NOT NULL,
  order_id text NOT NULL,
  payment_id text NOT NULL,
  provider text NOT NULL,
  provider_event_id text,
  amount numeric(18,6) NOT NULL,
  currency varchar(3) NOT NULL,
  payment_status text NOT NULL,
  environment text NOT NULL,
  correlation_id uuid NOT NULL,
  idempotency_key text NOT NULL,
  raw_event_hash varchar(64) NOT NULL,
  processing_status text NOT NULL DEFAULT 'received',
  processed_at timestamptz,
  processing_error text,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  reverses_event_id uuid,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_leoft_payment_events_event_id UNIQUE (event_id),
  CONSTRAINT uq_leoft_payment_events_provider_event UNIQUE (company_id, provider, provider_event_id),
  CONSTRAINT uq_leoft_payment_events_company_idempotency UNIQUE (company_id, idempotency_key),
  CONSTRAINT uq_leoft_payment_events_company_id UNIQUE (company_id, id),
  CONSTRAINT uq_leoft_payment_events_company_event_id UNIQUE (company_id, event_id),
  CONSTRAINT fk_leoft_payment_events_tenant_contact FOREIGN KEY (company_id, contact_id)
    REFERENCES crm_leads(empresa_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_leoft_payment_events_tenant_student FOREIGN KEY (company_id, student_id)
    REFERENCES crm_leads(empresa_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_leoft_payment_events_tenant_reversal FOREIGN KEY (company_id, reverses_event_id)
    REFERENCES leoft_payment_events(company_id, event_id) ON DELETE RESTRICT,
  CONSTRAINT ck_leoft_payment_events_type CHECK (event_type IN (
    'leoft.payment_confirmed.v1', 'leoft.payment_cancelled.v1', 'leoft.payment_refunded.v1'
  )),
  CONSTRAINT ck_leoft_payment_events_status CHECK (payment_status IN ('confirmed', 'cancelled', 'refunded')),
  CONSTRAINT ck_leoft_payment_events_type_status CHECK (
    (event_type = 'leoft.payment_confirmed.v1' AND payment_status = 'confirmed' AND reverses_event_id IS NULL)
    OR (event_type = 'leoft.payment_cancelled.v1' AND payment_status = 'cancelled' AND reverses_event_id IS NOT NULL)
    OR (event_type = 'leoft.payment_refunded.v1' AND payment_status = 'refunded' AND reverses_event_id IS NOT NULL)
  ),
  CONSTRAINT ck_leoft_payment_events_environment CHECK (environment IN ('e2e', 'staging', 'production')),
  CONSTRAINT ck_leoft_payment_events_processing_status CHECK (processing_status IN (
    'received','waiting_for_anamnesis','eligible','job_created','ignored','cancelled','failed'
  )),
  CONSTRAINT ck_leoft_payment_events_amount CHECK (amount > 0),
  CONSTRAINT ck_leoft_payment_events_currency CHECK (char_length(currency) = 3),
  CONSTRAINT ck_leoft_payment_events_hash CHECK (raw_event_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_leoft_payment_events_received CHECK (received_at >= occurred_at - interval '5 minutes'),
  CONSTRAINT ck_leoft_payment_events_required_text CHECK (
    btrim(product_id) <> '' AND btrim(order_id) <> '' AND btrim(payment_id) <> ''
    AND btrim(provider) <> '' AND (provider_event_id IS NULL OR btrim(provider_event_id) <> '')
    AND btrim(idempotency_key) <> ''
  )
);

CREATE TABLE IF NOT EXISTS leoft_anamnesis_snapshots (
  snapshot_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id uuid NOT NULL REFERENCES empresas(id) ON DELETE RESTRICT,
  student_id uuid NOT NULL,
  anamnesis_id text NOT NULL,
  anamnesis_version text NOT NULL,
  source_hash varchar(64) NOT NULL,
  captured_at timestamptz NOT NULL,
  goal text,
  experience_level text,
  age integer,
  biological_data jsonb,
  relevant_measurements jsonb,
  weekly_frequency integer,
  available_session_duration integer,
  available_equipment jsonb,
  limitations jsonb,
  injuries jsonb,
  preferences jsonb,
  restrictions jsonb,
  product_rules jsonb NOT NULL DEFAULT '{}'::jsonb,
  requested_duration_weeks integer NOT NULL,
  sessions_per_week integer NOT NULL,
  additional_answers jsonb NOT NULL DEFAULT '{}'::jsonb,
  schema_version text NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_leoft_anamnesis_snapshot_source UNIQUE (company_id, student_id, source_hash),
  CONSTRAINT uq_leoft_anamnesis_snapshots_company_id UNIQUE (company_id, snapshot_id),
  CONSTRAINT fk_leoft_anamnesis_snapshots_tenant_student FOREIGN KEY (company_id, student_id)
    REFERENCES crm_leads(empresa_id, id) ON DELETE RESTRICT,
  CONSTRAINT ck_leoft_anamnesis_snapshots_version CHECK (btrim(anamnesis_version) <> ''),
  CONSTRAINT ck_leoft_anamnesis_snapshots_hash CHECK (source_hash ~ '^[0-9a-f]{64}$'),
  CONSTRAINT ck_leoft_anamnesis_snapshots_age CHECK (age IS NULL OR age BETWEEN 1 AND 130),
  CONSTRAINT ck_leoft_anamnesis_snapshots_duration CHECK (requested_duration_weeks BETWEEN 1 AND 52),
  CONSTRAINT ck_leoft_anamnesis_snapshots_frequency CHECK (sessions_per_week BETWEEN 1 AND 14),
  CONSTRAINT ck_leoft_anamnesis_snapshots_weekly_frequency CHECK (weekly_frequency IS NULL OR weekly_frequency BETWEEN 1 AND 14),
  CONSTRAINT ck_leoft_anamnesis_snapshots_session_duration CHECK (available_session_duration IS NULL OR available_session_duration > 0),
  CONSTRAINT ck_leoft_anamnesis_snapshots_answers_object CHECK (jsonb_typeof(additional_answers) = 'object'),
  CONSTRAINT ck_leoft_anamnesis_snapshots_schema_version CHECK (btrim(schema_version) <> '')
);

CREATE TABLE IF NOT EXISTS leoft_generation_jobs (
  generation_job_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id uuid NOT NULL REFERENCES empresas(id) ON DELETE RESTRICT,
  student_id uuid NOT NULL,
  product_id text NOT NULL,
  order_id text NOT NULL,
  payment_event_id uuid NOT NULL,
  anamnesis_snapshot_id uuid NOT NULL,
  plan_id uuid,
  target_version_id uuid,
  latest_checkpoint_id uuid,
  status text NOT NULL DEFAULT 'pending',
  current_stage text,
  requested_duration_weeks integer NOT NULL,
  sessions_per_week integer NOT NULL,
  correlation_id uuid NOT NULL,
  idempotency_key text NOT NULL,
  hermes_request_id text,
  model_provider text,
  model_name text,
  attempt_count integer NOT NULL DEFAULT 0,
  maximum_attempts integer NOT NULL DEFAULT 3,
  input_tokens bigint NOT NULL DEFAULT 0,
  output_tokens bigint NOT NULL DEFAULT 0,
  estimated_cost numeric(18,8) NOT NULL DEFAULT 0,
  started_at timestamptz,
  completed_at timestamptz,
  failed_at timestamptz,
  last_error_code text,
  last_error_summary text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  row_version integer NOT NULL DEFAULT 0,
  CONSTRAINT uq_leoft_generation_jobs_company_idempotency UNIQUE (company_id, idempotency_key),
  CONSTRAINT uq_leoft_generation_jobs_company_id UNIQUE (company_id, generation_job_id),
  CONSTRAINT uq_leoft_generation_jobs_payment_event UNIQUE (company_id, payment_event_id),
  CONSTRAINT uq_leoft_generation_jobs_tenant_student_id UNIQUE (company_id, student_id, generation_job_id),
  CONSTRAINT fk_leoft_generation_jobs_tenant_student FOREIGN KEY (company_id, student_id)
    REFERENCES crm_leads(empresa_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_leoft_generation_jobs_tenant_payment FOREIGN KEY (company_id, payment_event_id)
    REFERENCES leoft_payment_events(company_id, id) ON DELETE RESTRICT,
  CONSTRAINT fk_leoft_generation_jobs_tenant_snapshot FOREIGN KEY (company_id, anamnesis_snapshot_id)
    REFERENCES leoft_anamnesis_snapshots(company_id, snapshot_id) ON DELETE RESTRICT,
  CONSTRAINT ck_leoft_generation_jobs_status CHECK (status IN (
    'pending','dispatching','planning','generating','validating','repairing',
    'awaiting_review','approved','publishing','published','failed','cancelled'
  )),
  CONSTRAINT ck_leoft_generation_jobs_duration CHECK (requested_duration_weeks BETWEEN 1 AND 52),
  CONSTRAINT ck_leoft_generation_jobs_frequency CHECK (sessions_per_week BETWEEN 1 AND 14),
  CONSTRAINT ck_leoft_generation_jobs_attempts CHECK (attempt_count >= 0 AND maximum_attempts > 0 AND attempt_count <= maximum_attempts),
  CONSTRAINT ck_leoft_generation_jobs_usage CHECK (input_tokens >= 0 AND output_tokens >= 0 AND estimated_cost >= 0),
  CONSTRAINT ck_leoft_generation_jobs_row_version CHECK (row_version >= 0),
  CONSTRAINT ck_leoft_generation_jobs_required_text CHECK (
    btrim(product_id) <> '' AND btrim(order_id) <> '' AND btrim(idempotency_key) <> ''
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_leoft_generation_jobs_active_purchase
  ON leoft_generation_jobs (company_id, product_id, order_id)
  WHERE status <> 'cancelled';

CREATE TABLE IF NOT EXISTS leoft_generation_session_tasks (
  session_task_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id uuid NOT NULL REFERENCES empresas(id) ON DELETE RESTRICT,
  generation_job_id uuid NOT NULL,
  target_version_id uuid NOT NULL REFERENCES leoft_training_plan_versions(id) ON DELETE RESTRICT,
  phase_number integer NOT NULL,
  week_number integer NOT NULL,
  session_number integer NOT NULL,
  session_code text NOT NULL,
  objective text NOT NULL,
  sequence integer NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  attempt_count integer NOT NULL DEFAULT 0,
  maximum_attempts integer NOT NULL DEFAULT 3,
  lease_owner text,
  lease_expires_at timestamptz,
  checkpoint_version integer NOT NULL DEFAULT 0,
  selected_provider text,
  selected_model text,
  input_tokens bigint NOT NULL DEFAULT 0,
  output_tokens bigint NOT NULL DEFAULT 0,
  estimated_cost numeric(18,8) NOT NULL DEFAULT 0,
  started_at timestamptz,
  completed_at timestamptz,
  failed_at timestamptz,
  last_error_code text,
  last_error_summary text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  row_version integer NOT NULL DEFAULT 0,
  CONSTRAINT uq_leoft_generation_tasks_job_week_session UNIQUE (generation_job_id, week_number, session_number),
  CONSTRAINT uq_leoft_generation_tasks_job_sequence UNIQUE (generation_job_id, sequence),
  CONSTRAINT uq_leoft_generation_tasks_company_id UNIQUE (company_id, session_task_id),
  CONSTRAINT fk_leoft_generation_tasks_tenant_job FOREIGN KEY (company_id, generation_job_id)
    REFERENCES leoft_generation_jobs(company_id, generation_job_id) ON DELETE CASCADE,
  CONSTRAINT ck_leoft_generation_tasks_status CHECK (status IN ('pending','generating','validating','repairing','completed','failed','cancelled')),
  CONSTRAINT ck_leoft_generation_tasks_coordinates CHECK (phase_number > 0 AND week_number > 0 AND session_number > 0 AND sequence > 0),
  CONSTRAINT ck_leoft_generation_tasks_attempts CHECK (attempt_count >= 0 AND maximum_attempts > 0 AND attempt_count <= maximum_attempts),
  CONSTRAINT ck_leoft_generation_tasks_usage CHECK (input_tokens >= 0 AND output_tokens >= 0 AND estimated_cost >= 0),
  CONSTRAINT ck_leoft_generation_tasks_versions CHECK (checkpoint_version >= 0 AND row_version >= 0),
  CONSTRAINT ck_leoft_generation_tasks_session_code CHECK (session_code ~ '^W[0-9]{2}S[0-9]{2}$'),
  CONSTRAINT ck_leoft_generation_tasks_lease CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL))
);

CREATE TABLE IF NOT EXISTS leoft_generation_attempts (
  attempt_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id uuid NOT NULL REFERENCES empresas(id) ON DELETE RESTRICT,
  generation_job_id uuid NOT NULL,
  session_task_id uuid,
  target_version_id uuid NOT NULL REFERENCES leoft_training_plan_versions(id) ON DELETE RESTRICT,
  attempt_number integer NOT NULL,
  attempt_type text NOT NULL,
  stage text NOT NULL,
  prompt_version text NOT NULL,
  catalog_version text NOT NULL,
  status text NOT NULL,
  provider text NOT NULL,
  model text NOT NULL,
  model_version text,
  provider_request_id text,
  input_tokens bigint NOT NULL DEFAULT 0,
  output_tokens bigint NOT NULL DEFAULT 0,
  total_tokens bigint NOT NULL DEFAULT 0,
  estimated_cost numeric(18,8) NOT NULL DEFAULT 0,
  currency varchar(3) NOT NULL,
  latency_ms integer NOT NULL DEFAULT 0,
  request_hash varchar(64) NOT NULL,
  response_hash varchar(64),
  validation_result jsonb,
  errors jsonb,
  repair_instructions jsonb,
  error_code text,
  error_summary text,
  started_at timestamptz NOT NULL,
  completed_at timestamptz,
  failed_at timestamptz,
  created_at timestamptz NOT NULL DEFAULT now(),
  correlation_id uuid NOT NULL,
  idempotency_key text NOT NULL,
  CONSTRAINT uq_leoft_generation_attempts_company_idempotency UNIQUE (company_id, idempotency_key),
  CONSTRAINT uq_leoft_generation_attempts_task_number UNIQUE (generation_job_id, session_task_id, attempt_number),
  CONSTRAINT fk_leoft_generation_attempts_tenant_job FOREIGN KEY (company_id, generation_job_id)
    REFERENCES leoft_generation_jobs(company_id, generation_job_id) ON DELETE CASCADE,
  CONSTRAINT fk_leoft_generation_attempts_tenant_task FOREIGN KEY (company_id, session_task_id)
    REFERENCES leoft_generation_session_tasks(company_id, session_task_id) ON DELETE CASCADE,
  CONSTRAINT ck_leoft_generation_attempts_number CHECK (attempt_number > 0),
  CONSTRAINT ck_leoft_generation_attempts_type CHECK (attempt_type IN ('blueprint','generation','validation','repair')),
  CONSTRAINT ck_leoft_generation_attempts_status CHECK (status IN (
    'started','succeeded','validation_failed','provider_failed','timed_out','cancelled'
  )),
  CONSTRAINT ck_leoft_generation_attempts_usage CHECK (
    input_tokens >= 0 AND output_tokens >= 0 AND total_tokens >= 0
    AND total_tokens = input_tokens + output_tokens AND estimated_cost >= 0 AND latency_ms >= 0
  ),
  CONSTRAINT ck_leoft_generation_attempts_currency CHECK (char_length(currency) = 3),
  CONSTRAINT ck_leoft_generation_attempts_hashes CHECK (
    request_hash ~ '^[0-9a-f]{64}$' AND (response_hash IS NULL OR response_hash ~ '^[0-9a-f]{64}$')
  ),
  CONSTRAINT ck_leoft_generation_attempts_versions CHECK (
    btrim(stage) <> '' AND btrim(prompt_version) <> '' AND btrim(catalog_version) <> ''
  )
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_leoft_generation_attempts_job_number
  ON leoft_generation_attempts(generation_job_id, attempt_number)
  WHERE session_task_id IS NULL;

CREATE TABLE IF NOT EXISTS leoft_generation_checkpoints (
  checkpoint_id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id uuid NOT NULL REFERENCES empresas(id) ON DELETE RESTRICT,
  generation_job_id uuid NOT NULL,
  checkpoint_version integer NOT NULL,
  target_version_id uuid NOT NULL REFERENCES leoft_training_plan_versions(id) ON DELETE RESTRICT,
  job_status text NOT NULL,
  stage text NOT NULL,
  current_session_task_id uuid,
  completed_task_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  pending_task_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  failed_task_ids jsonb NOT NULL DEFAULT '[]'::jsonb,
  blueprint_hash varchar(64) NOT NULL,
  snapshot_hash varchar(64) NOT NULL,
  state_hash varchar(64) NOT NULL,
  last_attempt_id uuid REFERENCES leoft_generation_attempts(attempt_id) ON DELETE RESTRICT,
  captured_at timestamptz NOT NULL,
  correlation_id uuid NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_leoft_generation_checkpoints_job_version UNIQUE (generation_job_id, checkpoint_version),
  CONSTRAINT fk_leoft_generation_checkpoints_tenant_job FOREIGN KEY (company_id, generation_job_id)
    REFERENCES leoft_generation_jobs(company_id, generation_job_id) ON DELETE CASCADE,
  CONSTRAINT fk_leoft_generation_checkpoints_tenant_task FOREIGN KEY (company_id, current_session_task_id)
    REFERENCES leoft_generation_session_tasks(company_id, session_task_id) ON DELETE RESTRICT,
  CONSTRAINT ck_leoft_generation_checkpoints_version CHECK (checkpoint_version > 0),
  CONSTRAINT ck_leoft_generation_checkpoints_task_lists CHECK (
    jsonb_typeof(completed_task_ids) = 'array' AND jsonb_typeof(pending_task_ids) = 'array'
    AND jsonb_typeof(failed_task_ids) = 'array'
  ),
  CONSTRAINT ck_leoft_generation_checkpoints_hashes CHECK (
    blueprint_hash ~ '^[0-9a-f]{64}$' AND snapshot_hash ~ '^[0-9a-f]{64}$'
    AND state_hash ~ '^[0-9a-f]{64}$'
  )
);

-- Composite keys used by deferrable tenant-scoped and cyclic references.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_leoft_training_plans_tenant_student_id') THEN
    ALTER TABLE leoft_training_plans ADD CONSTRAINT uq_leoft_training_plans_tenant_student_id UNIQUE (empresa_id, lead_id, id);
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_leoft_plan_versions_plan_id_id') THEN
    ALTER TABLE leoft_training_plan_versions ADD CONSTRAINT uq_leoft_plan_versions_plan_id_id UNIQUE (plan_id, id);
  END IF;
END $$;

DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_leoft_generation_jobs_tenant_plan') THEN
    ALTER TABLE leoft_generation_jobs ADD CONSTRAINT fk_leoft_generation_jobs_tenant_plan
      FOREIGN KEY (company_id, student_id, plan_id)
      REFERENCES leoft_training_plans(empresa_id, lead_id, id)
      ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_leoft_generation_jobs_target_version') THEN
    ALTER TABLE leoft_generation_jobs ADD CONSTRAINT fk_leoft_generation_jobs_target_version
      FOREIGN KEY (target_version_id) REFERENCES leoft_training_plan_versions(id)
      ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_leoft_generation_jobs_latest_checkpoint') THEN
    ALTER TABLE leoft_generation_jobs ADD CONSTRAINT fk_leoft_generation_jobs_latest_checkpoint
      FOREIGN KEY (latest_checkpoint_id) REFERENCES leoft_generation_checkpoints(checkpoint_id)
      ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_leoft_plans_tenant_generation_job') THEN
    ALTER TABLE leoft_training_plans ADD CONSTRAINT fk_leoft_plans_tenant_generation_job
      FOREIGN KEY (empresa_id, lead_id, generation_job_id)
      REFERENCES leoft_generation_jobs(company_id, student_id, generation_job_id)
      ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_leoft_plans_current_version') THEN
    ALTER TABLE leoft_training_plans ADD CONSTRAINT fk_leoft_plans_current_version
      FOREIGN KEY (id, current_version_id) REFERENCES leoft_training_plan_versions(plan_id, id)
      ON DELETE RESTRICT DEFERRABLE INITIALLY DEFERRED;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_leoft_plan_versions_source_snapshot') THEN
    ALTER TABLE leoft_training_plan_versions ADD CONSTRAINT fk_leoft_plan_versions_source_snapshot
      FOREIGN KEY (source_snapshot_id) REFERENCES leoft_anamnesis_snapshots(snapshot_id) ON DELETE RESTRICT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_leoft_training_sessions_session_task') THEN
    ALTER TABLE leoft_training_sessions ADD CONSTRAINT fk_leoft_training_sessions_session_task
      FOREIGN KEY (session_task_id) REFERENCES leoft_generation_session_tasks(session_task_id) ON DELETE SET NULL;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_leoft_training_exercises_catalog') THEN
    ALTER TABLE leoft_training_exercises ADD CONSTRAINT fk_leoft_training_exercises_catalog
      FOREIGN KEY (catalog_id) REFERENCES leoft_catalog_entries(catalog_id) ON DELETE RESTRICT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_leoft_training_exercises_media') THEN
    ALTER TABLE leoft_training_exercises ADD CONSTRAINT fk_leoft_training_exercises_media
      FOREIGN KEY (media_id) REFERENCES leoft_catalog_media(media_id) ON DELETE RESTRICT;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'fk_leoft_training_exercises_catalog_version') THEN
    ALTER TABLE leoft_training_exercises ADD CONSTRAINT fk_leoft_training_exercises_catalog_version
      FOREIGN KEY (catalog_version) REFERENCES leoft_catalog_versions(version) ON DELETE RESTRICT;
  END IF;
END $$;

-- Checks apply only to v2 values; nullable additions keep old records writable.
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_leoft_plan_v2_status') THEN
    ALTER TABLE leoft_training_plans ADD CONSTRAINT ck_leoft_plan_v2_status
      CHECK (product_id IS NULL OR status IN ('draft','generating','awaiting_review','approved','published','cancelled')) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_leoft_plan_versions_v2_status') THEN
    ALTER TABLE leoft_training_plan_versions ADD CONSTRAINT ck_leoft_plan_versions_v2_status
      CHECK (source_snapshot_id IS NULL OR status IN ('building','validating','awaiting_review','approved','rejected','revision_requested','published','superseded','cancelled')) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_leoft_plan_versions_schema_version') THEN
    ALTER TABLE leoft_training_plan_versions ADD CONSTRAINT ck_leoft_plan_versions_schema_version
      CHECK (schema_version IS NULL OR btrim(schema_version) <> '') NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_leoft_weeks_v2_values') THEN
    ALTER TABLE leoft_training_weeks ADD CONSTRAINT ck_leoft_weeks_v2_values
      CHECK (phase_number IS NULL OR (phase_number > 0 AND week_number > 0)) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_leoft_weeks_status') THEN
    ALTER TABLE leoft_training_weeks ADD CONSTRAINT ck_leoft_weeks_status
      CHECK (status IS NULL OR status IN ('pending','generating','validating','completed','failed','cancelled')) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_leoft_sessions_v2_values') THEN
    ALTER TABLE leoft_training_sessions ADD CONSTRAINT ck_leoft_sessions_v2_values
      CHECK (session_code IS NULL OR (session_code ~ '^W[0-9]{2}S[0-9]{2}$' AND sequence > 0 AND estimated_duration_minutes > 0)) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_leoft_sessions_v2_status') THEN
    ALTER TABLE leoft_training_sessions ADD CONSTRAINT ck_leoft_sessions_v2_status
      CHECK (status IS NULL OR status IN ('pending','generating','validating','completed','failed','cancelled')) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_leoft_sessions_estimated_duration') THEN
    ALTER TABLE leoft_training_sessions ADD CONSTRAINT ck_leoft_sessions_estimated_duration
      CHECK (estimated_duration_minutes IS NULL OR estimated_duration_minutes > 0) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_leoft_exercises_sets') THEN
    ALTER TABLE leoft_training_exercises ADD CONSTRAINT ck_leoft_exercises_sets
      CHECK (sets IS NULL OR sets > 0) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_leoft_exercises_duration_seconds') THEN
    ALTER TABLE leoft_training_exercises ADD CONSTRAINT ck_leoft_exercises_duration_seconds
      CHECK (duration_seconds IS NULL OR duration_seconds >= 0) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_leoft_exercises_rest_seconds') THEN
    ALTER TABLE leoft_training_exercises ADD CONSTRAINT ck_leoft_exercises_rest_seconds
      CHECK (rest_seconds IS NULL OR rest_seconds >= 0) NOT VALID;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'ck_leoft_exercises_v2_prescription') THEN
    ALTER TABLE leoft_training_exercises ADD CONSTRAINT ck_leoft_exercises_v2_prescription CHECK (
      prescription_mode IS NULL OR (
        "order" > 0 AND rest_seconds >= 0 AND catalog_id IS NOT NULL AND media_id IS NOT NULL
        AND catalog_version IS NOT NULL AND association_validated_at IS NOT NULL
        AND (
          (prescription_mode = 'repetitions' AND sets > 0 AND repetitions IS NOT NULL AND duration_seconds IS NULL)
          OR (prescription_mode = 'duration' AND duration_seconds > 0 AND repetitions IS NULL AND (sets IS NULL OR sets > 0))
          OR (prescription_mode = 'interval' AND progression_rule IS NOT NULL AND repetitions IS NULL AND duration_seconds IS NULL)
        )
      )
    ) NOT VALID;
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS ux_leoft_training_sessions_version_code
  ON leoft_training_sessions(version_id, session_code) WHERE session_code IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS ux_leoft_training_exercises_session_order
  ON leoft_training_exercises(session_id, "order") WHERE "order" IS NOT NULL;
CREATE INDEX IF NOT EXISTS ix_leoft_payment_events_purchase ON leoft_payment_events(company_id, product_id, order_id, created_at);
CREATE INDEX IF NOT EXISTS ix_leoft_payment_events_student ON leoft_payment_events(company_id, student_id, created_at);
CREATE INDEX IF NOT EXISTS ix_leoft_anamnesis_snapshots_student ON leoft_anamnesis_snapshots(company_id, student_id, captured_at);
CREATE INDEX IF NOT EXISTS ix_leoft_generation_jobs_status_created ON leoft_generation_jobs(company_id, status, created_at);
CREATE INDEX IF NOT EXISTS ix_leoft_generation_jobs_correlation ON leoft_generation_jobs(correlation_id);
CREATE INDEX IF NOT EXISTS ix_leoft_generation_tasks_claim ON leoft_generation_session_tasks(generation_job_id, status, lease_expires_at, sequence);
CREATE INDEX IF NOT EXISTS ix_leoft_generation_attempts_job_created ON leoft_generation_attempts(generation_job_id, created_at);
CREATE INDEX IF NOT EXISTS ix_leoft_generation_attempts_task_created ON leoft_generation_attempts(session_task_id, created_at);
CREATE INDEX IF NOT EXISTS ix_leoft_generation_checkpoints_job_captured ON leoft_generation_checkpoints(generation_job_id, captured_at DESC);

CREATE OR REPLACE FUNCTION leoft_generation_append_only_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION '% is append-only', TG_TABLE_NAME USING ERRCODE = '55000';
END;
$$;

DROP TRIGGER IF EXISTS trg_leoft_anamnesis_snapshots_append_only ON leoft_anamnesis_snapshots;
CREATE TRIGGER trg_leoft_anamnesis_snapshots_append_only
  BEFORE UPDATE OR DELETE ON leoft_anamnesis_snapshots
  FOR EACH ROW EXECUTE FUNCTION leoft_generation_append_only_guard();

DROP TRIGGER IF EXISTS trg_leoft_generation_attempts_append_only ON leoft_generation_attempts;
CREATE TRIGGER trg_leoft_generation_attempts_append_only
  BEFORE UPDATE OR DELETE ON leoft_generation_attempts
  FOR EACH ROW EXECUTE FUNCTION leoft_generation_append_only_guard();

DROP TRIGGER IF EXISTS trg_leoft_generation_checkpoints_append_only ON leoft_generation_checkpoints;
CREATE TRIGGER trg_leoft_generation_checkpoints_append_only
  BEFORE UPDATE OR DELETE ON leoft_generation_checkpoints
  FOR EACH ROW EXECUTE FUNCTION leoft_generation_append_only_guard();

CREATE OR REPLACE FUNCTION leoft_generation_job_transition_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.anamnesis_snapshot_id IS DISTINCT FROM OLD.anamnesis_snapshot_id AND OLD.status <> 'pending' THEN
    RAISE EXCEPTION 'anamnesis snapshot cannot change after pending' USING ERRCODE = '23514';
  END IF;
  IF NEW.target_version_id IS DISTINCT FROM OLD.target_version_id
     AND (OLD.target_version_id IS NOT NULL OR OLD.status <> 'pending') THEN
    RAISE EXCEPTION 'target version is immutable after first pending assignment' USING ERRCODE = '23514';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
    (OLD.status = 'pending' AND NEW.status IN ('dispatching','cancelled')) OR
    (OLD.status = 'dispatching' AND NEW.status IN ('planning','failed','cancelled')) OR
    (OLD.status = 'planning' AND NEW.status IN ('generating','failed','cancelled')) OR
    (OLD.status = 'generating' AND NEW.status IN ('validating','failed','cancelled')) OR
    (OLD.status = 'validating' AND NEW.status IN ('awaiting_review','repairing','failed','cancelled')) OR
    (OLD.status = 'repairing' AND NEW.status IN ('generating','validating','failed','cancelled')) OR
    (OLD.status = 'awaiting_review' AND NEW.status IN ('approved','cancelled')) OR
    (OLD.status = 'approved' AND NEW.status IN ('publishing','cancelled')) OR
    (OLD.status = 'publishing' AND NEW.status IN ('published','failed','cancelled'))
  ) THEN
    RAISE EXCEPTION 'invalid generation job transition: % -> %', OLD.status, NEW.status USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_leoft_generation_jobs_transition ON leoft_generation_jobs;
CREATE TRIGGER trg_leoft_generation_jobs_transition
  BEFORE UPDATE ON leoft_generation_jobs
  FOR EACH ROW EXECUTE FUNCTION leoft_generation_job_transition_guard();

CREATE OR REPLACE FUNCTION leoft_generation_task_transition_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status IN ('completed','failed','cancelled')
     AND (to_jsonb(NEW) - 'checkpoint_version') IS DISTINCT FROM (to_jsonb(OLD) - 'checkpoint_version') THEN
    RAISE EXCEPTION 'terminal generation task is immutable' USING ERRCODE = '55000';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
    (OLD.status = 'pending' AND NEW.status IN ('generating','cancelled')) OR
    (OLD.status = 'generating' AND NEW.status IN ('validating','failed','cancelled')) OR
    (OLD.status = 'validating' AND NEW.status IN ('completed','repairing','failed','cancelled')) OR
    (OLD.status = 'repairing' AND NEW.status IN ('generating','failed','cancelled'))
  ) THEN
    RAISE EXCEPTION 'invalid generation task transition: % -> %', OLD.status, NEW.status USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS trg_leoft_generation_tasks_transition ON leoft_generation_session_tasks;
CREATE TRIGGER trg_leoft_generation_tasks_transition
  BEFORE UPDATE ON leoft_generation_session_tasks
  FOR EACH ROW EXECUTE FUNCTION leoft_generation_task_transition_guard();

COMMIT;

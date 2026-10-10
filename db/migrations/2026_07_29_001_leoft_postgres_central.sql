CREATE TABLE IF NOT EXISTS leoft_training_plan_versions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id uuid NOT NULL REFERENCES leoft_training_plans(id) ON DELETE CASCADE,
  version_number integer NOT NULL,
  schema_version text,
  source text,
  status text,
  snapshot_json jsonb NOT NULL,
  checksum text NOT NULL,
  created_at timestamp NOT NULL DEFAULT now(),
  created_by text,
  superseded_at timestamp,
  CONSTRAINT uq_leoft_plan_version_number UNIQUE(plan_id, version_number)
);
CREATE INDEX IF NOT EXISTS ix_leoft_plan_versions_plan_id ON leoft_training_plan_versions(plan_id);
CREATE INDEX IF NOT EXISTS ix_leoft_plan_versions_checksum ON leoft_training_plan_versions(checksum);

CREATE TABLE IF NOT EXISTS leoft_training_weeks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id uuid NOT NULL REFERENCES leoft_training_plans(id) ON DELETE CASCADE,
  version_id uuid NOT NULL REFERENCES leoft_training_plan_versions(id) ON DELETE CASCADE,
  week_number integer NOT NULL CHECK (week_number > 0),
  phase text, focus text, position integer NOT NULL,
  source_json jsonb, created_at timestamp NOT NULL DEFAULT now(), updated_at timestamp NOT NULL DEFAULT now(),
  CONSTRAINT uq_leoft_week_version_number UNIQUE(version_id, week_number)
);
CREATE INDEX IF NOT EXISTS ix_leoft_training_weeks_plan_id ON leoft_training_weeks(plan_id);

CREATE TABLE IF NOT EXISTS leoft_training_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id uuid NOT NULL REFERENCES leoft_training_plans(id) ON DELETE CASCADE,
  version_id uuid NOT NULL REFERENCES leoft_training_plan_versions(id) ON DELETE CASCADE,
  week_id uuid NOT NULL REFERENCES leoft_training_weeks(id) ON DELETE CASCADE,
  external_session_id text NOT NULL, position integer NOT NULL, day_label text, title text, session_type text,
  warmup text, cooldown text, estimated_duration text, notes jsonb, source_json jsonb,
  created_at timestamp NOT NULL DEFAULT now(), updated_at timestamp NOT NULL DEFAULT now(),
  CONSTRAINT uq_leoft_session_version_external_id UNIQUE(version_id, external_session_id)
);
CREATE INDEX IF NOT EXISTS ix_leoft_training_sessions_week_position ON leoft_training_sessions(week_id, position);

CREATE TABLE IF NOT EXISTS leoft_training_exercises (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  plan_id uuid NOT NULL REFERENCES leoft_training_plans(id) ON DELETE CASCADE,
  version_id uuid NOT NULL REFERENCES leoft_training_plan_versions(id) ON DELETE CASCADE,
  session_id uuid NOT NULL REFERENCES leoft_training_sessions(id) ON DELETE CASCADE,
  external_exercise_id text NOT NULL, position integer NOT NULL, name text NOT NULL, muscle_group text,
  sets integer, reps text, duration text, rest text, intensity text, instructions text, gif_url text,
  alternatives jsonb, caution_tags jsonb, source_json jsonb, created_at timestamp NOT NULL DEFAULT now(), updated_at timestamp NOT NULL DEFAULT now(),
  CONSTRAINT uq_leoft_exercise_session_position UNIQUE(session_id, position)
);
CREATE INDEX IF NOT EXISTS ix_leoft_training_exercises_session_position ON leoft_training_exercises(session_id, position);

CREATE TABLE IF NOT EXISTS leoft_training_review_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), review_public_id text UNIQUE NOT NULL,
  plan_id uuid NOT NULL REFERENCES leoft_training_plans(id) ON DELETE CASCADE,
  version_id uuid NOT NULL REFERENCES leoft_training_plan_versions(id) ON DELETE RESTRICT,
  status text NOT NULL CHECK(status IN ('pending','reviewing','awaiting_confirmation','approving','approved','failed','cancelled','expired')),
  current_page integer NOT NULL DEFAULT 0 CHECK(current_page >= 0), total_pages integer NOT NULL CHECK(total_pages >= 1),
  telegram_chat_id text, telegram_message_id text, telegram_user_id text, reviewer_identifier text,
  idempotency_key text UNIQUE, optimistic_lock integer NOT NULL DEFAULT 0,
  confirmation_requested_at timestamp, approved_at timestamp, approved_by text, failed_at timestamp,
  failure_code text, failure_message_sanitized text, last_callback_id_hash text,
  created_at timestamp NOT NULL DEFAULT now(), updated_at timestamp NOT NULL DEFAULT now(), expires_at timestamp
);
CREATE INDEX IF NOT EXISTS ix_leoft_review_sessions_plan_id ON leoft_training_review_sessions(plan_id);

CREATE TABLE IF NOT EXISTS leoft_training_review_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), review_session_id uuid NOT NULL REFERENCES leoft_training_review_sessions(id) ON DELETE CASCADE,
  plan_id uuid NOT NULL REFERENCES leoft_training_plans(id) ON DELETE CASCADE, event_type text NOT NULL,
  page_before integer, page_after integer, actor_type text, actor_identifier text, callback_id_hash text,
  idempotency_key text, payload_sanitized jsonb, result_status text, created_at timestamp NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_leoft_review_events_session ON leoft_training_review_events(review_session_id);
CREATE INDEX IF NOT EXISTS ix_leoft_review_events_plan ON leoft_training_review_events(plan_id);
CREATE INDEX IF NOT EXISTS ix_leoft_review_events_type_created ON leoft_training_review_events(event_type, created_at);

CREATE TABLE IF NOT EXISTS leoft_integration_events (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(), direction text NOT NULL, integration text NOT NULL, event_type text NOT NULL,
  external_event_id text, idempotency_key text NOT NULL, plan_id uuid REFERENCES leoft_training_plans(id) ON DELETE SET NULL,
  review_session_id uuid REFERENCES leoft_training_review_sessions(id) ON DELETE SET NULL, request_hash text,
  request_payload_sanitized jsonb, response_status integer, response_payload_sanitized jsonb, attempt_count integer NOT NULL DEFAULT 1,
  status text NOT NULL, next_retry_at timestamp, processed_at timestamp, last_error_code text, last_error_sanitized text,
  created_at timestamp NOT NULL DEFAULT now(), updated_at timestamp NOT NULL DEFAULT now(),
  CONSTRAINT uq_leoft_integration_idempotency UNIQUE(integration, idempotency_key)
);
CREATE INDEX IF NOT EXISTS ix_leoft_integration_events_plan ON leoft_integration_events(plan_id);
CREATE INDEX IF NOT EXISTS ix_leoft_integration_events_status_retry ON leoft_integration_events(status, next_retry_at);

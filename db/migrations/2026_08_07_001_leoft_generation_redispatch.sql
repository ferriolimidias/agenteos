ALTER TABLE leoft_generation_jobs
  ADD COLUMN IF NOT EXISTS dispatch_attempt_count integer NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS last_dispatch_attempt_at timestamptz,
  ADD COLUMN IF NOT EXISTS last_redispatch_reason text;

ALTER TABLE leoft_generation_jobs
  DROP CONSTRAINT IF EXISTS ck_leoft_generation_jobs_dispatch_attempt_count;

ALTER TABLE leoft_generation_jobs
  ADD CONSTRAINT ck_leoft_generation_jobs_dispatch_attempt_count
  CHECK (dispatch_attempt_count >= 0);

CREATE INDEX IF NOT EXISTS ix_leoft_generation_jobs_dispatch_recovery
  ON leoft_generation_jobs(company_id, status, current_stage, last_dispatch_attempt_at);

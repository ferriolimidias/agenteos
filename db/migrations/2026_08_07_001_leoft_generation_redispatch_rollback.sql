DROP INDEX IF EXISTS ix_leoft_generation_jobs_dispatch_recovery;

ALTER TABLE leoft_generation_jobs
  DROP CONSTRAINT IF EXISTS ck_leoft_generation_jobs_dispatch_attempt_count,
  DROP COLUMN IF EXISTS last_redispatch_reason,
  DROP COLUMN IF EXISTS last_dispatch_attempt_at,
  DROP COLUMN IF EXISTS dispatch_attempt_count;

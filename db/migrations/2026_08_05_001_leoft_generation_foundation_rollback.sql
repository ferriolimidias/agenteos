BEGIN;

DROP TRIGGER IF EXISTS trg_leoft_generation_jobs_transition ON leoft_generation_jobs;
DROP TRIGGER IF EXISTS trg_leoft_generation_tasks_transition ON leoft_generation_session_tasks;
DROP TRIGGER IF EXISTS trg_leoft_anamnesis_snapshots_append_only ON leoft_anamnesis_snapshots;
DROP TRIGGER IF EXISTS trg_leoft_generation_attempts_append_only ON leoft_generation_attempts;
DROP TRIGGER IF EXISTS trg_leoft_generation_checkpoints_append_only ON leoft_generation_checkpoints;
DROP FUNCTION IF EXISTS leoft_generation_job_transition_guard();
DROP FUNCTION IF EXISTS leoft_generation_task_transition_guard();
DROP FUNCTION IF EXISTS leoft_generation_append_only_guard();

ALTER TABLE IF EXISTS leoft_training_plans
  DROP CONSTRAINT IF EXISTS fk_leoft_plans_tenant_generation_job,
  DROP CONSTRAINT IF EXISTS fk_leoft_plans_current_version,
  DROP CONSTRAINT IF EXISTS ck_leoft_plan_v2_status;
ALTER TABLE IF EXISTS leoft_training_plan_versions
  DROP CONSTRAINT IF EXISTS fk_leoft_plan_versions_source_snapshot,
  DROP CONSTRAINT IF EXISTS ck_leoft_plan_versions_v2_status,
  DROP CONSTRAINT IF EXISTS ck_leoft_plan_versions_schema_version;
ALTER TABLE IF EXISTS leoft_training_weeks
  DROP CONSTRAINT IF EXISTS ck_leoft_weeks_v2_values,
  DROP CONSTRAINT IF EXISTS ck_leoft_weeks_status;
ALTER TABLE IF EXISTS leoft_training_sessions
  DROP CONSTRAINT IF EXISTS fk_leoft_training_sessions_session_task,
  DROP CONSTRAINT IF EXISTS ck_leoft_sessions_v2_values,
  DROP CONSTRAINT IF EXISTS ck_leoft_sessions_v2_status,
  DROP CONSTRAINT IF EXISTS ck_leoft_sessions_estimated_duration;
ALTER TABLE IF EXISTS leoft_training_exercises
  DROP CONSTRAINT IF EXISTS fk_leoft_training_exercises_catalog,
  DROP CONSTRAINT IF EXISTS fk_leoft_training_exercises_media,
  DROP CONSTRAINT IF EXISTS fk_leoft_training_exercises_catalog_version,
  DROP CONSTRAINT IF EXISTS ck_leoft_exercises_v2_prescription,
  DROP CONSTRAINT IF EXISTS ck_leoft_exercises_sets,
  DROP CONSTRAINT IF EXISTS ck_leoft_exercises_duration_seconds,
  DROP CONSTRAINT IF EXISTS ck_leoft_exercises_rest_seconds;
ALTER TABLE IF EXISTS leoft_generation_jobs
  DROP CONSTRAINT IF EXISTS fk_leoft_generation_jobs_latest_checkpoint;

DROP INDEX IF EXISTS ux_leoft_training_sessions_version_code;
DROP INDEX IF EXISTS ux_leoft_training_exercises_session_order;
DROP INDEX IF EXISTS ix_leoft_payment_events_purchase;
DROP INDEX IF EXISTS ix_leoft_payment_events_student;
DROP INDEX IF EXISTS ix_leoft_anamnesis_snapshots_student;
DROP INDEX IF EXISTS ix_leoft_generation_jobs_status_created;
DROP INDEX IF EXISTS ix_leoft_generation_jobs_correlation;
DROP INDEX IF EXISTS ix_leoft_generation_tasks_claim;
DROP INDEX IF EXISTS ix_leoft_generation_attempts_job_created;
DROP INDEX IF EXISTS ix_leoft_generation_attempts_task_created;
DROP INDEX IF EXISTS ux_leoft_generation_attempts_job_number;
DROP INDEX IF EXISTS ix_leoft_generation_checkpoints_job_captured;

DROP TABLE IF EXISTS leoft_generation_checkpoints;
DROP TABLE IF EXISTS leoft_generation_attempts;
DROP TABLE IF EXISTS leoft_generation_session_tasks;
DROP TABLE IF EXISTS leoft_generation_jobs;
DROP TABLE IF EXISTS leoft_anamnesis_snapshots;
DROP TABLE IF EXISTS leoft_payment_events;

ALTER TABLE IF EXISTS leoft_training_plan_versions
  DROP CONSTRAINT IF EXISTS uq_leoft_plan_versions_plan_id_id;
ALTER TABLE IF EXISTS leoft_training_plans
  DROP CONSTRAINT IF EXISTS uq_leoft_training_plans_tenant_student_id;

ALTER TABLE IF EXISTS leoft_training_exercises
  DROP COLUMN IF EXISTS association_validated_at,
  DROP COLUMN IF EXISTS selection_reason,
  DROP COLUMN IF EXISTS canonical_name,
  DROP COLUMN IF EXISTS prescription_mode,
  DROP COLUMN IF EXISTS substitutions,
  DROP COLUMN IF EXISTS progression_rule,
  DROP COLUMN IF EXISTS notes,
  DROP COLUMN IF EXISTS load_guidance,
  DROP COLUMN IF EXISTS tempo,
  DROP COLUMN IF EXISTS rest_seconds,
  DROP COLUMN IF EXISTS duration_seconds,
  DROP COLUMN IF EXISTS repetitions,
  DROP COLUMN IF EXISTS "order",
  DROP COLUMN IF EXISTS catalog_version,
  DROP COLUMN IF EXISTS media_id,
  DROP COLUMN IF EXISTS catalog_id;
ALTER TABLE IF EXISTS leoft_training_sessions
  DROP COLUMN IF EXISTS status,
  DROP COLUMN IF EXISTS estimated_duration_minutes,
  DROP COLUMN IF EXISTS sequence,
  DROP COLUMN IF EXISTS objective,
  DROP COLUMN IF EXISTS session_code,
  DROP COLUMN IF EXISTS session_task_id;
ALTER TABLE IF EXISTS leoft_training_weeks
  DROP COLUMN IF EXISTS status,
  DROP COLUMN IF EXISTS progression_notes,
  DROP COLUMN IF EXISTS objective,
  DROP COLUMN IF EXISTS phase_number;
ALTER TABLE IF EXISTS leoft_training_plan_versions
  DROP COLUMN IF EXISTS updated_at,
  DROP COLUMN IF EXISTS approved_by,
  DROP COLUMN IF EXISTS published_at,
  DROP COLUMN IF EXISTS approved_at,
  DROP COLUMN IF EXISTS submitted_for_review_at,
  DROP COLUMN IF EXISTS generation_completed_at,
  DROP COLUMN IF EXISTS content_hash,
  DROP COLUMN IF EXISTS blueprint,
  DROP COLUMN IF EXISTS source_snapshot_id;
ALTER TABLE IF EXISTS leoft_training_plans
  DROP COLUMN IF EXISTS current_version_id,
  DROP COLUMN IF EXISTS generation_job_id,
  DROP COLUMN IF EXISTS product_id;

-- Restore the pre-S3 status domain without deleting v2-status rows that may remain.
ALTER TABLE IF EXISTS leoft_training_plans DROP CONSTRAINT IF EXISTS ck_leoft_training_plans_status;
DO $$ BEGIN
  IF to_regclass('public.leoft_training_plans') IS NOT NULL THEN
    ALTER TABLE leoft_training_plans ADD CONSTRAINT ck_leoft_training_plans_status
      CHECK (status IN ('pending_approval','approved','rejected','revision_requested')) NOT VALID;
  END IF;
END $$;

COMMIT;

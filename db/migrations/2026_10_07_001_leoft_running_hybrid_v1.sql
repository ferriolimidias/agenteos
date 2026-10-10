-- Running Sessions V1, Hybrid Training V1 and Weekly Model Scheduling V1.
-- Additive/idempotent. Legacy manual sessions default to STRENGTH.
BEGIN;

ALTER TABLE public.leoft_manual_workouts
  ADD COLUMN IF NOT EXISTS session_type text NOT NULL DEFAULT 'STRENGTH';
UPDATE public.leoft_manual_workouts
   SET session_type = CASE
     WHEN upper(coalesce(session_type, '')) IN ('RUNNING','HYBRID','STRENGTH') THEN upper(session_type)
     WHEN upper(coalesce(modality, '')) LIKE '%RUN%' THEN 'RUNNING'
     WHEN upper(coalesce(modality, '')) LIKE '%HYBRID%' OR upper(coalesce(modality, '')) LIKE '%HÍBRID%' THEN 'HYBRID'
     ELSE 'STRENGTH' END
 WHERE session_type IS NULL OR session_type NOT IN ('STRENGTH','RUNNING','HYBRID');
ALTER TABLE public.leoft_manual_workouts
  DROP CONSTRAINT IF EXISTS ck_leoft_manual_workouts_session_type;
ALTER TABLE public.leoft_manual_workouts
  ADD CONSTRAINT ck_leoft_manual_workouts_session_type CHECK (session_type IN ('STRENGTH','RUNNING','HYBRID'));

ALTER TABLE public.leoft_training_program_sessions
  ADD COLUMN IF NOT EXISTS session_type text NOT NULL DEFAULT 'STRENGTH',
  ADD COLUMN IF NOT EXISTS weekday smallint,
  ADD COLUMN IF NOT EXISTS week_order integer,
  ADD COLUMN IF NOT EXISTS optional boolean NOT NULL DEFAULT false;
UPDATE public.leoft_training_program_sessions ps
   SET session_type = coalesce(nullif(w.session_type, ''), 'STRENGTH'),
       week_order = coalesce(ps.week_order, ps.session_order)
  FROM public.leoft_manual_workouts w
 WHERE w.id = ps.workout_id;
ALTER TABLE public.leoft_training_program_sessions
  DROP CONSTRAINT IF EXISTS ck_leoft_training_program_sessions_session_type;
ALTER TABLE public.leoft_training_program_sessions
  ADD CONSTRAINT ck_leoft_training_program_sessions_session_type CHECK (session_type IN ('STRENGTH','RUNNING','HYBRID'));
ALTER TABLE public.leoft_training_program_sessions
  DROP CONSTRAINT IF EXISTS ck_leoft_training_program_sessions_weekday;
ALTER TABLE public.leoft_training_program_sessions
  ADD CONSTRAINT ck_leoft_training_program_sessions_weekday CHECK (weekday IS NULL OR weekday BETWEEN 1 AND 7);
ALTER TABLE public.leoft_training_program_sessions
  DROP CONSTRAINT IF EXISTS ck_leoft_training_program_sessions_week_order;
ALTER TABLE public.leoft_training_program_sessions
  ADD CONSTRAINT ck_leoft_training_program_sessions_week_order CHECK (week_order IS NULL OR week_order > 0);
CREATE INDEX IF NOT EXISTS ix_leoft_training_program_sessions_weekly
  ON public.leoft_training_program_sessions(program_id, weekday, week_order, session_order);

CREATE TABLE IF NOT EXISTS public.leoft_running_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workout_id uuid NOT NULL UNIQUE REFERENCES public.leoft_manual_workouts(id) ON DELETE CASCADE,
  objective text,
  duration_seconds integer CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
  distance_meters numeric(12,2) CHECK (distance_meters IS NULL OR distance_meters >= 0),
  intensity_type text CHECK (intensity_type IS NULL OR intensity_type IN ('RPE','PACE','HEART_RATE_ZONE','POWER')),
  intensity_min numeric(8,2),
  intensity_max numeric(8,2),
  heart_rate_zone text,
  pace_min_seconds_per_km integer CHECK (pace_min_seconds_per_km IS NULL OR pace_min_seconds_per_km >= 0),
  pace_max_seconds_per_km integer CHECK (pace_max_seconds_per_km IS NULL OR pace_max_seconds_per_km >= 0),
  power_min_watts integer CHECK (power_min_watts IS NULL OR power_min_watts >= 0),
  power_max_watts integer CHECK (power_max_watts IS NULL OR power_max_watts >= 0),
  terrain text CHECK (terrain IS NULL OR terrain IN ('ROAD','TREADMILL','TRACK','TRAIL','OTHER')),
  notes text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_leoft_running_sessions_workout ON public.leoft_running_sessions(workout_id);

CREATE TABLE IF NOT EXISTS public.leoft_running_repeats (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workout_id uuid NOT NULL REFERENCES public.leoft_manual_workouts(id) ON DELETE CASCADE,
  position integer NOT NULL CHECK (position > 0),
  repeat_count integer NOT NULL CHECK (repeat_count BETWEEN 2 AND 99),
  label text,
  created_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_leoft_running_repeat_position UNIQUE (workout_id, position)
);
CREATE TABLE IF NOT EXISTS public.leoft_running_blocks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workout_id uuid NOT NULL REFERENCES public.leoft_manual_workouts(id) ON DELETE CASCADE,
  repeat_id uuid REFERENCES public.leoft_running_repeats(id) ON DELETE CASCADE,
  position integer NOT NULL CHECK (position > 0),
  block_type text NOT NULL CHECK (block_type IN ('WARMUP','RUN','RECOVERY','INTERVAL','COOLDOWN')),
  title text,
  duration_seconds integer CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
  distance_meters numeric(12,2) CHECK (distance_meters IS NULL OR distance_meters >= 0),
  intensity_type text CHECK (intensity_type IS NULL OR intensity_type IN ('RPE','PACE','HEART_RATE_ZONE','POWER')),
  intensity_min numeric(8,2),
  intensity_max numeric(8,2),
  heart_rate_zone text,
  pace_min_seconds_per_km integer CHECK (pace_min_seconds_per_km IS NULL OR pace_min_seconds_per_km >= 0),
  pace_max_seconds_per_km integer CHECK (pace_max_seconds_per_km IS NULL OR pace_max_seconds_per_km >= 0),
  power_min_watts integer CHECK (power_min_watts IS NULL OR power_min_watts >= 0),
  power_max_watts integer CHECK (power_max_watts IS NULL OR power_max_watts >= 0),
  terrain text CHECK (terrain IS NULL OR terrain IN ('ROAD','TREADMILL','TRACK','TRAIL','OTHER')),
  notes text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_leoft_running_block_measure CHECK (duration_seconds IS NOT NULL OR distance_meters IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS ix_leoft_running_blocks_workout_position ON public.leoft_running_blocks(workout_id, position);
CREATE INDEX IF NOT EXISTS ix_leoft_running_blocks_repeat ON public.leoft_running_blocks(repeat_id, position);

CREATE TABLE IF NOT EXISTS public.leoft_hybrid_sessions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workout_id uuid NOT NULL UNIQUE REFERENCES public.leoft_manual_workouts(id) ON DELETE CASCADE,
  running_workout_id uuid NOT NULL REFERENCES public.leoft_manual_workouts(id) ON DELETE RESTRICT,
  strength_label text,
  running_label text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT ck_leoft_hybrid_distinct_workouts CHECK (workout_id <> running_workout_id)
);
CREATE INDEX IF NOT EXISTS ix_leoft_hybrid_sessions_running_workout ON public.leoft_hybrid_sessions(running_workout_id);

ALTER TABLE public.leoft_workout_executions
  ADD COLUMN IF NOT EXISTS session_type text NOT NULL DEFAULT 'STRENGTH',
  ADD COLUMN IF NOT EXISTS actual_duration_seconds integer,
  ADD COLUMN IF NOT EXISTS actual_rpe numeric(3,1),
  ADD COLUMN IF NOT EXISTS distance_actual_meters numeric(12,2),
  ADD COLUMN IF NOT EXISTS average_pace_seconds_per_km integer,
  ADD COLUMN IF NOT EXISTS average_heart_rate integer,
  ADD COLUMN IF NOT EXISTS average_power_watts integer;
ALTER TABLE public.leoft_workout_executions
  DROP CONSTRAINT IF EXISTS ck_leoft_workout_executions_session_type;
ALTER TABLE public.leoft_workout_executions
  ADD CONSTRAINT ck_leoft_workout_executions_session_type CHECK (session_type IN ('STRENGTH','RUNNING','HYBRID'));

CREATE TABLE IF NOT EXISTS public.leoft_workout_execution_blocks (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  execution_id uuid NOT NULL REFERENCES public.leoft_workout_executions(id) ON DELETE CASCADE,
  source_block_id uuid,
  position integer NOT NULL CHECK (position > 0),
  block_type text NOT NULL,
  repeat_index integer,
  repeat_count integer,
  prescription_snapshot jsonb NOT NULL DEFAULT '{}'::jsonb,
  status text NOT NULL DEFAULT 'pending' CHECK (status IN ('pending','completed','skipped')),
  completed_at timestamptz,
  actual_duration_seconds integer CHECK (actual_duration_seconds IS NULL OR actual_duration_seconds >= 0),
  actual_distance_meters numeric(12,2) CHECK (actual_distance_meters IS NULL OR actual_distance_meters >= 0),
  actual_rpe numeric(3,1),
  notes text,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_leoft_workout_execution_block_position UNIQUE (execution_id, position)
);
CREATE INDEX IF NOT EXISTS ix_leoft_workout_execution_blocks_execution ON public.leoft_workout_execution_blocks(execution_id, position);

COMMIT;

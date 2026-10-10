ALTER TABLE leoft_catalog_entries
  ADD COLUMN IF NOT EXISTS empresa_id uuid REFERENCES empresas(id) ON DELETE CASCADE,
  ADD COLUMN IF NOT EXISTS description text,
  ADD COLUMN IF NOT EXISTS instructions text,
  ADD COLUMN IF NOT EXISTS modality text,
  ADD COLUMN IF NOT EXISTS media_url text,
  ADD COLUMN IF NOT EXISTS active boolean NOT NULL DEFAULT true;

CREATE INDEX IF NOT EXISTS ix_leoft_catalog_entries_empresa_id
  ON leoft_catalog_entries (empresa_id);

CREATE INDEX IF NOT EXISTS ix_leoft_catalog_entries_active_name
  ON leoft_catalog_entries (active, normalized_name);

CREATE TABLE IF NOT EXISTS leoft_manual_workouts (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  empresa_id uuid NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
  name text NOT NULL,
  description text,
  modality text,
  active boolean NOT NULL DEFAULT true,
  created_at timestamp NOT NULL DEFAULT now(),
  updated_at timestamp NOT NULL DEFAULT now(),
  CONSTRAINT ck_leoft_manual_workouts_name CHECK (btrim(name) <> '')
);

CREATE INDEX IF NOT EXISTS ix_leoft_manual_workouts_empresa_id
  ON leoft_manual_workouts (empresa_id);

CREATE INDEX IF NOT EXISTS ix_leoft_manual_workouts_active_updated
  ON leoft_manual_workouts (empresa_id, active, updated_at DESC);

CREATE TABLE IF NOT EXISTS leoft_manual_workout_exercises (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  workout_id uuid NOT NULL REFERENCES leoft_manual_workouts(id) ON DELETE CASCADE,
  exercise_id uuid NOT NULL REFERENCES leoft_catalog_entries(id) ON DELETE RESTRICT,
  position integer NOT NULL,
  sets integer,
  reps text,
  duration_seconds integer,
  rest_seconds integer,
  load text,
  notes text,
  created_at timestamp NOT NULL DEFAULT now(),
  updated_at timestamp NOT NULL DEFAULT now(),
  CONSTRAINT uq_leoft_manual_workout_exercise_position UNIQUE (workout_id, position),
  CONSTRAINT ck_leoft_manual_workout_exercise_position CHECK (position > 0),
  CONSTRAINT ck_leoft_manual_workout_exercise_sets CHECK (sets IS NULL OR sets > 0),
  CONSTRAINT ck_leoft_manual_workout_exercise_duration CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
  CONSTRAINT ck_leoft_manual_workout_exercise_rest CHECK (rest_seconds IS NULL OR rest_seconds >= 0)
);

CREATE INDEX IF NOT EXISTS ix_leoft_manual_workout_exercises_workout_position
  ON leoft_manual_workout_exercises (workout_id, position);

CREATE INDEX IF NOT EXISTS ix_leoft_manual_workout_exercises_exercise_id
  ON leoft_manual_workout_exercises (exercise_id);

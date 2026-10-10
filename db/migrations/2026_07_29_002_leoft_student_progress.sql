CREATE TABLE IF NOT EXISTS leoft_student_exercise_progress (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  empresa_id uuid NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
  access_id uuid NOT NULL REFERENCES leoft_student_accesses(id) ON DELETE CASCADE,
  plan_id uuid NOT NULL REFERENCES leoft_training_plans(id) ON DELETE CASCADE,
  exercise_id text NOT NULL,
  workout_id text NOT NULL,
  status text NOT NULL DEFAULT 'completed' CHECK (status = 'completed'),
  completed_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_leoft_student_exercise_progress UNIQUE (access_id, plan_id, exercise_id)
);
CREATE INDEX IF NOT EXISTS ix_leoft_student_exercise_progress_empresa_id ON leoft_student_exercise_progress (empresa_id);
CREATE INDEX IF NOT EXISTS ix_leoft_student_exercise_progress_access_plan ON leoft_student_exercise_progress (access_id, plan_id);
CREATE INDEX IF NOT EXISTS ix_leoft_student_exercise_progress_plan_workout ON leoft_student_exercise_progress (plan_id, workout_id);

CREATE TABLE IF NOT EXISTS leoft_student_workout_progress (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  empresa_id uuid NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
  access_id uuid NOT NULL REFERENCES leoft_student_accesses(id) ON DELETE CASCADE,
  plan_id uuid NOT NULL REFERENCES leoft_training_plans(id) ON DELETE CASCADE,
  workout_id text NOT NULL,
  completion_source text NOT NULL DEFAULT 'manual' CHECK (completion_source IN ('manual', 'automatic')),
  status text NOT NULL DEFAULT 'completed' CHECK (status = 'completed'),
  completed_at timestamptz NOT NULL DEFAULT now(),
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_leoft_student_workout_progress UNIQUE (access_id, plan_id, workout_id)
);
CREATE INDEX IF NOT EXISTS ix_leoft_student_workout_progress_empresa_id ON leoft_student_workout_progress (empresa_id);
CREATE INDEX IF NOT EXISTS ix_leoft_student_workout_progress_access_plan ON leoft_student_workout_progress (access_id, plan_id);
CREATE INDEX IF NOT EXISTS ix_leoft_student_workout_progress_plan ON leoft_student_workout_progress (plan_id, workout_id);

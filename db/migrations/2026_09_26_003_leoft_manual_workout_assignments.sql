DO $$ BEGIN
  ALTER TABLE leoft_students
    ADD CONSTRAINT uq_leoft_students_id_empresa UNIQUE (id, empresa_id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

DO $$ BEGIN
  ALTER TABLE leoft_manual_workouts
    ADD CONSTRAINT uq_leoft_manual_workouts_id_empresa UNIQUE (id, empresa_id);
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS leoft_student_workout_assignments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  empresa_id uuid NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
  student_id uuid NOT NULL,
  workout_id uuid NOT NULL,
  position integer NOT NULL,
  status text NOT NULL DEFAULT 'blocked',
  released_at timestamp,
  blocked_at timestamp,
  created_at timestamp NOT NULL DEFAULT now(),
  updated_at timestamp NOT NULL DEFAULT now(),
  CONSTRAINT fk_leoft_student_workout_assignments_student_tenant
    FOREIGN KEY (student_id, empresa_id)
    REFERENCES leoft_students(id, empresa_id) ON DELETE CASCADE,
  CONSTRAINT fk_leoft_student_workout_assignments_workout_tenant
    FOREIGN KEY (workout_id, empresa_id)
    REFERENCES leoft_manual_workouts(id, empresa_id) ON DELETE CASCADE,
  CONSTRAINT uq_leoft_student_workout_assignment_pair UNIQUE (student_id, workout_id),
  CONSTRAINT uq_leoft_student_workout_assignment_position
    UNIQUE (student_id, position) DEFERRABLE INITIALLY DEFERRED,
  CONSTRAINT ck_leoft_student_workout_assignment_position CHECK (position > 0),
  CONSTRAINT ck_leoft_student_workout_assignment_status CHECK (status IN ('blocked', 'released'))
);

CREATE INDEX IF NOT EXISTS ix_leoft_student_workout_assignments_tenant_student_position
  ON leoft_student_workout_assignments (empresa_id, student_id, position);

CREATE INDEX IF NOT EXISTS ix_leoft_student_workout_assignments_workout
  ON leoft_student_workout_assignments (empresa_id, workout_id);

CREATE INDEX IF NOT EXISTS ix_leoft_student_workout_assignments_released
  ON leoft_student_workout_assignments (student_id, status)
  WHERE status = 'released';

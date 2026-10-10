DROP TABLE IF EXISTS leoft_student_workout_assignments;
ALTER TABLE leoft_manual_workouts DROP CONSTRAINT IF EXISTS uq_leoft_manual_workouts_id_empresa;
ALTER TABLE leoft_students DROP CONSTRAINT IF EXISTS uq_leoft_students_id_empresa;

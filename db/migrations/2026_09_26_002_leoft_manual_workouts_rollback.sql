DROP TABLE IF EXISTS leoft_manual_workout_exercises;
DROP TABLE IF EXISTS leoft_manual_workouts;

DROP INDEX IF EXISTS ix_leoft_catalog_entries_active_name;
DROP INDEX IF EXISTS ix_leoft_catalog_entries_empresa_id;

ALTER TABLE leoft_catalog_entries
  DROP COLUMN IF EXISTS active,
  DROP COLUMN IF EXISTS media_url,
  DROP COLUMN IF EXISTS modality,
  DROP COLUMN IF EXISTS instructions,
  DROP COLUMN IF EXISTS description,
  DROP COLUMN IF EXISTS empresa_id;

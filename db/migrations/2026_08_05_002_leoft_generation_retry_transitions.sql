BEGIN;

CREATE OR REPLACE FUNCTION leoft_generation_task_transition_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.status IN ('completed','failed','cancelled')
     AND (to_jsonb(NEW) - 'checkpoint_version') IS DISTINCT FROM (to_jsonb(OLD) - 'checkpoint_version') THEN
    RAISE EXCEPTION 'terminal generation task is immutable' USING ERRCODE = '55000';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
    (OLD.status = 'pending' AND NEW.status IN ('generating','cancelled')) OR
    (OLD.status = 'generating' AND NEW.status IN ('pending','validating','failed','cancelled')) OR
    (OLD.status = 'validating' AND NEW.status IN ('pending','completed','repairing','failed','cancelled')) OR
    (OLD.status = 'repairing' AND NEW.status IN ('pending','generating','failed','cancelled'))
  ) THEN
    RAISE EXCEPTION 'invalid generation task transition: % -> %', OLD.status, NEW.status USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END;
$$;

COMMIT;

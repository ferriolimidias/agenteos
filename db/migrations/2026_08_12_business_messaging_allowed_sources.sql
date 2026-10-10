BEGIN;

ALTER TABLE meta_conversion_events
    ADD COLUMN IF NOT EXISTS trigger_source VARCHAR NULL;

COMMIT;

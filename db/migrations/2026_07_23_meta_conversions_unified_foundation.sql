-- Minimal, reversible schema support for the single Meta conversion pipeline.
ALTER TABLE tag_meta_conversion_configs ADD COLUMN IF NOT EXISTS conversion_context VARCHAR NOT NULL DEFAULT 'current';
ALTER TABLE tag_meta_conversion_configs ADD COLUMN IF NOT EXISTS credential_source VARCHAR NULL;
ALTER TABLE meta_conversion_events ADD COLUMN IF NOT EXISTS conversion_context VARCHAR NOT NULL DEFAULT 'current';
ALTER TABLE meta_conversion_events ADD COLUMN IF NOT EXISTS action_source VARCHAR NULL;
ALTER TABLE meta_conversion_events ADD COLUMN IF NOT EXISTS messaging_channel VARCHAR NULL;
ALTER TABLE meta_conversion_events ADD COLUMN IF NOT EXISTS credential_source VARCHAR NULL;
ALTER TABLE meta_conversion_events ADD COLUMN IF NOT EXISTS waba_id_masked VARCHAR NULL;
ALTER TABLE meta_conversion_events ADD COLUMN IF NOT EXISTS dataset_id_masked VARCHAR NULL;
ALTER TABLE meta_conversion_events ADD COLUMN IF NOT EXISTS ctwa_present BOOLEAN NOT NULL DEFAULT FALSE;

-- downgrade
-- ALTER TABLE meta_conversion_events DROP COLUMN IF EXISTS ctwa_present, DROP COLUMN IF EXISTS dataset_id_masked, DROP COLUMN IF EXISTS waba_id_masked, DROP COLUMN IF EXISTS credential_source, DROP COLUMN IF EXISTS messaging_channel, DROP COLUMN IF EXISTS action_source, DROP COLUMN IF EXISTS conversion_context;
-- ALTER TABLE tag_meta_conversion_configs DROP COLUMN IF EXISTS credential_source, DROP COLUMN IF EXISTS conversion_context;

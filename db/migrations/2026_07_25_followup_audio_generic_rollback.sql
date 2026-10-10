BEGIN;

ALTER TABLE followup_sequence_steps DROP COLUMN IF EXISTS audio_caption;
ALTER TABLE followup_sequence_steps DROP COLUMN IF EXISTS send_as_voice;
ALTER TABLE followup_sequence_steps DROP COLUMN IF EXISTS media_asset_id;
ALTER TABLE followup_sequence_steps DROP COLUMN IF EXISTS content_type;
DROP TABLE IF EXISTS media_assets;

COMMIT;

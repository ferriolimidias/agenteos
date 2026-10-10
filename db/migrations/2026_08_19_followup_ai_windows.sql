-- Generic follow-up delivery controls.
-- Additive only: existing follow-ups remain fixed_text with no send window restriction.

ALTER TABLE followup_sequence_steps
    ADD COLUMN IF NOT EXISTS message_mode varchar NOT NULL DEFAULT 'fixed_text';

ALTER TABLE followup_sequence_steps
    ADD COLUMN IF NOT EXISTS send_window_enabled boolean NOT NULL DEFAULT false;

ALTER TABLE followup_sequence_steps
    ADD COLUMN IF NOT EXISTS send_window_start time NULL;

ALTER TABLE followup_sequence_steps
    ADD COLUMN IF NOT EXISTS send_window_end time NULL;

ALTER TABLE followup_sequence_steps
    ADD COLUMN IF NOT EXISTS limit_to_free_entry_window boolean NOT NULL DEFAULT false;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_followup_sequence_steps_message_mode'
    ) THEN
        ALTER TABLE followup_sequence_steps
            ADD CONSTRAINT ck_followup_sequence_steps_message_mode
            CHECK (message_mode IN ('fixed_text', 'ai_contextual', 'meta_template'));
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_followup_sequence_steps_message_mode
    ON followup_sequence_steps (empresa_id, message_mode, status);

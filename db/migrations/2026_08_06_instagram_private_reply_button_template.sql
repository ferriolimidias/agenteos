-- Add structured Instagram private reply configuration for text/button templates.
-- Backward compatible: existing private_message/private_link values remain untouched.

ALTER TABLE instagram_comment_automations
    ADD COLUMN IF NOT EXISTS private_reply_config JSONB NULL;

-- Rollback Fase A: remove apenas a fundacao operacional criada nesta fase.
-- Nao remove dados legados, channel_contacts ou channel_conversations da Fase 2.

DROP TABLE IF EXISTS message_status_events;
DROP TABLE IF EXISTS communication_consents;
DROP TABLE IF EXISTS message_attempts;
DROP TABLE IF EXISTS message_outbox;
DROP TABLE IF EXISTS followup_executions;
DROP TABLE IF EXISTS campaign_recipients;

ALTER TABLE config_followups DROP CONSTRAINT IF EXISTS uq_config_followups_empresa_id_id;
ALTER TABLE campanhas_disparo DROP CONSTRAINT IF EXISTS uq_campanhas_disparo_empresa_id_id;

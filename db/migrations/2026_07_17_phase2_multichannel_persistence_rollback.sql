-- Rollback destrutivo de schema para ambiente isolado/teste.
-- Dados gravados nas tabelas/colunas multicanal apos o upgrade serao descartados.
-- Nao aplicar em producao sem exportacao/backup aprovado em gate humano.
-- Preserva tabelas legadas, PKs legadas e FKs simples existentes antes da Fase 2.

DROP INDEX IF EXISTS ix_mensagens_historico_external_message;
DROP INDEX IF EXISTS ix_mensagens_historico_channel_conversation;
DROP INDEX IF EXISTS ix_channel_conversations_contact;
DROP INDEX IF EXISTS ix_channel_conversations_lead;
DROP INDEX IF EXISTS ix_channel_contacts_normalized_address;
DROP INDEX IF EXISTS ix_channel_contacts_lead;
DROP INDEX IF EXISTS ix_conexoes_multichannel_lookup;

ALTER TABLE mensagens_historico DROP CONSTRAINT IF EXISTS fk_mensagens_historico_empresa_conversation;
ALTER TABLE mensagens_historico DROP CONSTRAINT IF EXISTS fk_mensagens_historico_empresa_conexao;
ALTER TABLE mensagens_historico DROP CONSTRAINT IF EXISTS fk_mensagens_historico_empresa_lead;
ALTER TABLE mensagens_historico DROP CONSTRAINT IF EXISTS mensagens_historico_channel_conversation_id_fkey;
ALTER TABLE mensagens_historico DROP CONSTRAINT IF EXISTS mensagens_historico_empresa_id_fkey;
ALTER TABLE IF EXISTS channel_conversations DROP CONSTRAINT IF EXISTS fk_channel_conversations_empresa_lead;
ALTER TABLE IF EXISTS channel_conversations DROP CONSTRAINT IF EXISTS fk_channel_conversations_empresa_contact;
ALTER TABLE IF EXISTS channel_conversations DROP CONSTRAINT IF EXISTS fk_channel_conversations_empresa_conexao;
ALTER TABLE IF EXISTS channel_conversations DROP CONSTRAINT IF EXISTS channel_conversations_lead_id_fkey;
ALTER TABLE IF EXISTS channel_conversations DROP CONSTRAINT IF EXISTS channel_conversations_contact_id_fkey;
ALTER TABLE IF EXISTS channel_conversations DROP CONSTRAINT IF EXISTS channel_conversations_conexao_id_fkey;
ALTER TABLE IF EXISTS channel_conversations DROP CONSTRAINT IF EXISTS channel_conversations_empresa_id_fkey;
ALTER TABLE IF EXISTS channel_contacts DROP CONSTRAINT IF EXISTS fk_channel_contacts_empresa_lead;
ALTER TABLE IF EXISTS channel_contacts DROP CONSTRAINT IF EXISTS fk_channel_contacts_empresa_conexao;
ALTER TABLE IF EXISTS channel_contacts DROP CONSTRAINT IF EXISTS channel_contacts_lead_id_fkey;
ALTER TABLE IF EXISTS channel_contacts DROP CONSTRAINT IF EXISTS channel_contacts_conexao_id_fkey;
ALTER TABLE IF EXISTS channel_contacts DROP CONSTRAINT IF EXISTS channel_contacts_empresa_id_fkey;
ALTER TABLE IF EXISTS channel_conversations DROP CONSTRAINT IF EXISTS uq_channel_conversations_empresa_id_id;
ALTER TABLE IF EXISTS channel_contacts DROP CONSTRAINT IF EXISTS uq_channel_contacts_empresa_id_id;

ALTER TABLE mensagens_historico DROP COLUMN IF EXISTS metadata_json;
ALTER TABLE mensagens_historico DROP COLUMN IF EXISTS provider_status;
ALTER TABLE mensagens_historico DROP COLUMN IF EXISTS message_status;
ALTER TABLE mensagens_historico DROP COLUMN IF EXISTS direction;
ALTER TABLE mensagens_historico DROP COLUMN IF EXISTS external_conversation_id;
ALTER TABLE mensagens_historico DROP COLUMN IF EXISTS external_message_id;
ALTER TABLE mensagens_historico DROP COLUMN IF EXISTS provider;
ALTER TABLE mensagens_historico DROP COLUMN IF EXISTS channel;
ALTER TABLE mensagens_historico DROP COLUMN IF EXISTS channel_conversation_id;
ALTER TABLE mensagens_historico DROP COLUMN IF EXISTS empresa_id;

DROP TABLE IF EXISTS channel_conversations;
DROP TABLE IF EXISTS channel_contacts;

ALTER TABLE crm_leads DROP CONSTRAINT IF EXISTS uq_crm_leads_empresa_id_id;
ALTER TABLE conexoes DROP CONSTRAINT IF EXISTS uq_conexoes_empresa_id_id;

ALTER TABLE conexoes DROP COLUMN IF EXISTS atualizado_em;
ALTER TABLE conexoes DROP COLUMN IF EXISTS metadata_json;
ALTER TABLE conexoes DROP COLUMN IF EXISTS display_number;
ALTER TABLE conexoes DROP COLUMN IF EXISTS page_id;
ALTER TABLE conexoes DROP COLUMN IF EXISTS instagram_business_account_id;
ALTER TABLE conexoes DROP COLUMN IF EXISTS phone_number_id;
ALTER TABLE conexoes DROP COLUMN IF EXISTS waba_id;
ALTER TABLE conexoes DROP COLUMN IF EXISTS external_account_id;
ALTER TABLE conexoes DROP COLUMN IF EXISTS is_default;
ALTER TABLE conexoes DROP COLUMN IF EXISTS provider;
ALTER TABLE conexoes DROP COLUMN IF EXISTS channel;

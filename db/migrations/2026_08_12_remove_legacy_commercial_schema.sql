ALTER TABLE IF EXISTS meta_conversion_events
  DROP COLUMN IF EXISTS tag_config_id;
DROP TABLE IF EXISTS tag_meta_conversion_configs;
ALTER TABLE IF EXISTS empresas DROP COLUMN IF EXISTS condutor_prompt;
ALTER TABLE IF EXISTS empresas DROP COLUMN IF EXISTS condutor_ativo;
ALTER TABLE IF EXISTS empresas DROP COLUMN IF EXISTS modelo_roteador;
DROP TABLE IF EXISTS especialista_ferramentas;
DROP TABLE IF EXISTS especialista_tools;
DROP TABLE IF EXISTS especialistas;

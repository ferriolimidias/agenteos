-- Migrate useful conductor guidance into the canonical business-rules field.
UPDATE empresas
SET ia_regras_negocio = CASE
    WHEN COALESCE(BTRIM(condutor_prompt), '') = '' THEN ia_regras_negocio
    WHEN COALESCE(ia_regras_negocio, '') LIKE '%' || BTRIM(condutor_prompt) || '%' THEN ia_regras_negocio
    ELSE CONCAT_WS(E'\n\n', NULLIF(ia_regras_negocio, ''), 'Orientação de condução comercial:', BTRIM(condutor_prompt))
  END,
  condutor_prompt = NULL,
  credenciais_canais = COALESCE(credenciais_canais, '{}'::jsonb) - 'funnel_routing' - 'sequencias_whatsapp';

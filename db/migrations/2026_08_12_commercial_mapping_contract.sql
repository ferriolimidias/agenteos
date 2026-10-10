-- Move high-quality milestones from tag triggers to tenant-owned CRM stages.
-- No tenant identifiers are embedded; rows are changed only when the
-- corresponding event and a semantically named stage exist for that tenant.
UPDATE business_messaging_conversion_mappings AS mapping
SET trigger_type = 'crm_stage_changed',
    trigger_ref = stage.id::text,
    atualizado_em = NOW()
FROM crm_etapas AS stage
JOIN crm_funis AS funnel ON funnel.id = stage.funil_id
WHERE mapping.trigger_type = 'tag_applied'
  AND mapping.event_name IN ('QualifiedLead', 'InitiateCheckout')
  AND mapping.empresa_id = funnel.empresa_id
  AND (
    (mapping.event_name = 'QualifiedLead' AND LOWER(stage.nome) LIKE '%qualific%')
    OR
    (mapping.event_name = 'InitiateCheckout' AND (LOWER(stage.nome) LIKE '%checkout%' OR LOWER(stage.nome) LIKE '%anamnese%'))
  )
  AND NOT EXISTS (
    SELECT 1
    FROM business_messaging_conversion_mappings AS duplicate
    WHERE duplicate.empresa_id = mapping.empresa_id
      AND duplicate.event_name = mapping.event_name
      AND duplicate.trigger_type = 'crm_stage_changed'
      AND duplicate.trigger_ref = stage.id::text
      AND duplicate.id <> mapping.id
  );

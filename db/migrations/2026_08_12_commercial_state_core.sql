ALTER TABLE tags_crm ADD COLUMN IF NOT EXISTS ai_can_apply BOOLEAN NOT NULL DEFAULT FALSE;

DO $$
DECLARE
    empresa_record RECORD;
    tag_id_value TEXT;
BEGIN
    FOR empresa_record IN SELECT id, credenciais_canais FROM empresas LOOP
        FOR tag_id_value IN
            SELECT jsonb_array_elements_text(COALESCE(empresa_record.credenciais_canais -> 'tags_ia_habilitadas', '[]'::jsonb))
        LOOP
            UPDATE tags_crm
               SET ai_can_apply = TRUE
             WHERE id = tag_id_value::uuid
               AND empresa_id = empresa_record.id;
        END LOOP;
        UPDATE empresas
           SET credenciais_canais = COALESCE(credenciais_canais, '{}'::jsonb) - 'tags_ia_habilitadas'
         WHERE id = empresa_record.id
           AND credenciais_canais ? 'tags_ia_habilitadas';
    END LOOP;
END $$;

CREATE INDEX IF NOT EXISTS ix_tags_crm_empresa_ai_apply ON tags_crm (empresa_id, ai_can_apply);

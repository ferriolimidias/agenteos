BEGIN;

DO $$
BEGIN
    IF to_regclass('public.empresas') IS NULL THEN
        RAISE EXCEPTION 'precheck failed: empresas missing';
    END IF;
    IF to_regclass('public.conexoes') IS NULL THEN
        RAISE EXCEPTION 'precheck failed: conexoes missing';
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_conexoes_empresa_id_id') THEN
        RAISE EXCEPTION 'precheck failed: uq_conexoes_empresa_id_id missing';
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS meta_message_templates (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    conexao_id UUID NOT NULL REFERENCES conexoes(id) ON DELETE CASCADE,
    waba_id TEXT NOT NULL,
    remote_template_id TEXT NULL,
    name TEXT NOT NULL,
    language TEXT NOT NULL,
    raw_category TEXT NULL,
    normalized_category TEXT NOT NULL DEFAULT 'unknown',
    raw_status TEXT NULL,
    normalized_status TEXT NOT NULL DEFAULT 'unknown',
    quality_rating TEXT NULL,
    rejection_reason TEXT NULL,
    parameter_format TEXT NULL,
    remote_created_at TIMESTAMP NULL,
    remote_updated_at TIMESTAMP NULL,
    last_synced_at TIMESTAMP NULL,
    first_seen_at TIMESTAMP NOT NULL DEFAULT now(),
    last_seen_at TIMESTAMP NOT NULL DEFAULT now(),
    stale_at TIMESTAMP NULL,
    archived_at TIMESTAMP NULL,
    graph_api_version TEXT NOT NULL DEFAULT 'v25.0',
    raw_metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT fk_meta_templates_empresa_conexao FOREIGN KEY (empresa_id, conexao_id) REFERENCES conexoes(empresa_id, id) ON DELETE CASCADE,
    CONSTRAINT uq_meta_templates_empresa_id_id UNIQUE (empresa_id, id),
    CONSTRAINT uq_meta_templates_remote_id UNIQUE (empresa_id, conexao_id, remote_template_id),
    CONSTRAINT uq_meta_templates_name_language_waba UNIQUE (empresa_id, conexao_id, waba_id, name, language)
);

CREATE TABLE IF NOT EXISTS meta_template_components (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    template_id UUID NOT NULL REFERENCES meta_message_templates(id) ON DELETE CASCADE,
    component_type TEXT NOT NULL,
    component_order INTEGER NOT NULL,
    format TEXT NULL,
    text TEXT NULL,
    buttons_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    example_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    media_reference_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    raw_component_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    updated_at TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT fk_meta_template_components_empresa_template FOREIGN KEY (empresa_id, template_id) REFERENCES meta_message_templates(empresa_id, id) ON DELETE CASCADE,
    CONSTRAINT uq_meta_template_components_order UNIQUE (empresa_id, template_id, component_order)
);

CREATE TABLE IF NOT EXISTS meta_template_sync_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    conexao_id UUID NOT NULL REFERENCES conexoes(id) ON DELETE CASCADE,
    waba_id TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'started',
    started_at TIMESTAMP NOT NULL DEFAULT now(),
    completed_at TIMESTAMP NULL,
    initial_cursor TEXT NULL,
    final_cursor TEXT NULL,
    pages INTEGER NOT NULL DEFAULT 0,
    found_count INTEGER NOT NULL DEFAULT 0,
    created_count INTEGER NOT NULL DEFAULT 0,
    updated_count INTEGER NOT NULL DEFAULT 0,
    unchanged_count INTEGER NOT NULL DEFAULT 0,
    stale_count INTEGER NOT NULL DEFAULT 0,
    error_code TEXT NULL,
    error_detail TEXT NULL,
    graph_api_version TEXT NOT NULL DEFAULT 'v25.0',
    source TEXT NOT NULL DEFAULT 'manual',
    initiated_by UUID NULL REFERENCES usuarios(id) ON DELETE SET NULL,
    idempotency_key TEXT NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT now(),
    CONSTRAINT fk_meta_template_sync_runs_empresa_conexao FOREIGN KEY (empresa_id, conexao_id) REFERENCES conexoes(empresa_id, id) ON DELETE CASCADE,
    CONSTRAINT uq_meta_template_sync_runs_idempotency UNIQUE (empresa_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS ix_meta_templates_lookup ON meta_message_templates (empresa_id, conexao_id, waba_id, normalized_status, normalized_category);
CREATE INDEX IF NOT EXISTS ix_meta_templates_search ON meta_message_templates (empresa_id, name, language);
CREATE INDEX IF NOT EXISTS ix_meta_template_components_template ON meta_template_components (empresa_id, template_id, component_type);
CREATE INDEX IF NOT EXISTS ix_meta_template_sync_runs_lookup ON meta_template_sync_runs (empresa_id, conexao_id, waba_id, started_at);

DO $$
BEGIN
    IF to_regclass('public.meta_message_templates') IS NULL THEN
        RAISE EXCEPTION 'postcheck failed: meta_message_templates missing';
    END IF;
    IF to_regclass('public.meta_template_components') IS NULL THEN
        RAISE EXCEPTION 'postcheck failed: meta_template_components missing';
    END IF;
    IF to_regclass('public.meta_template_sync_runs') IS NULL THEN
        RAISE EXCEPTION 'postcheck failed: meta_template_sync_runs missing';
    END IF;
END $$;

COMMIT;

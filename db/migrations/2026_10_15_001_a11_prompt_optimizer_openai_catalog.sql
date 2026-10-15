ALTER TABLE empresas
    ADD COLUMN IF NOT EXISTS modelo_ia_request_profile VARCHAR(32) NOT NULL DEFAULT 'legacy';

CREATE TABLE IF NOT EXISTS openai_model_catalog_snapshots (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    credential_fingerprint VARCHAR(64) NOT NULL,
    provider VARCHAR(32) NOT NULL DEFAULT 'openai',
    project_context_fingerprint VARCHAR(64) NOT NULL DEFAULT 'default',
    models_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    fetched_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_openai_catalog_scope UNIQUE (empresa_id, credential_fingerprint, provider, project_context_fingerprint)
);
CREATE INDEX IF NOT EXISTS ix_openai_model_catalog_snapshots_empresa_id ON openai_model_catalog_snapshots(empresa_id);

CREATE TABLE IF NOT EXISTS openai_model_validations (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    credential_fingerprint VARCHAR(64) NOT NULL,
    model_id VARCHAR(255) NOT NULL,
    endpoint VARCHAR(64) NOT NULL DEFAULT 'chat.completions',
    validation_status VARCHAR(24) NOT NULL CHECK (validation_status IN ('compatible', 'incompatible')),
    validated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    CONSTRAINT uq_openai_model_validation_scope UNIQUE (empresa_id, credential_fingerprint, model_id)
);
CREATE INDEX IF NOT EXISTS ix_openai_model_validations_empresa_id ON openai_model_validations(empresa_id);

CREATE TABLE IF NOT EXISTS a11_prompt_versions (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    module_key VARCHAR(64) NOT NULL,
    version_number INTEGER NOT NULL CHECK (version_number > 0),
    content TEXT NOT NULL,
    content_hash VARCHAR(64) NOT NULL,
    source_version_id UUID NULL REFERENCES a11_prompt_versions(id) ON DELETE SET NULL,
    change_summary TEXT NOT NULL,
    hypothesis TEXT NOT NULL,
    primary_metric VARCHAR(64) NOT NULL,
    author_id VARCHAR(128),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_a11_prompt_version UNIQUE (empresa_id, module_key, version_number)
);
CREATE INDEX IF NOT EXISTS ix_a11_prompt_versions_tenant_module ON a11_prompt_versions(empresa_id, module_key, version_number);

CREATE TABLE IF NOT EXISTS a11_prompt_module_states (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    module_key VARCHAR(64) NOT NULL,
    stable_version_id UUID NULL REFERENCES a11_prompt_versions(id) ON DELETE RESTRICT,
    active_version_id UUID NULL REFERENCES a11_prompt_versions(id) ON DELETE RESTRICT,
    state VARCHAR(24) NOT NULL DEFAULT 'OFFLINE' CHECK (state IN ('OFFLINE','SHADOW','READY_FOR_PILOT','LIVE_DISABLED','PAUSED','ROLLED_BACK')),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_a11_module_state UNIQUE (empresa_id, module_key)
);

CREATE TABLE IF NOT EXISTS a11_optimizer_settings (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    evaluator_model_id VARCHAR(255),
    evaluator_enabled BOOLEAN NOT NULL DEFAULT FALSE,
    updated_by VARCHAR(128),
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_a11_optimizer_settings_tenant UNIQUE (empresa_id)
);

CREATE TABLE IF NOT EXISTS a11_prompt_experiments (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    module_key VARCHAR(64) NOT NULL,
    control_version_id UUID NOT NULL REFERENCES a11_prompt_versions(id) ON DELETE RESTRICT,
    candidate_version_id UUID NOT NULL REFERENCES a11_prompt_versions(id) ON DELETE RESTRICT,
    assignment_unit VARCHAR(32) NOT NULL DEFAULT 'conversation' CHECK (assignment_unit = 'conversation'),
    candidate_percent INTEGER NOT NULL DEFAULT 50 CHECK (candidate_percent BETWEEN 1 AND 99),
    status VARCHAR(24) NOT NULL DEFAULT 'DRAFT' CHECK (status IN ('DRAFT','READY_FOR_PILOT','RUNNING','PAUSED','COMPLETED','ROLLED_BACK')),
    starts_at TIMESTAMP,
    ends_at TIMESTAMP,
    criteria_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    last_auto_evaluated_at TIMESTAMP,
    created_by VARCHAR(128),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
ALTER TABLE a11_prompt_experiments ADD COLUMN IF NOT EXISTS last_auto_evaluated_at TIMESTAMP;
CREATE INDEX IF NOT EXISTS ix_a11_experiment_tenant_state ON a11_prompt_experiments(empresa_id, status);
CREATE UNIQUE INDEX IF NOT EXISTS uq_a11_one_running_experiment_per_tenant ON a11_prompt_experiments(empresa_id) WHERE status = 'RUNNING';

CREATE TABLE IF NOT EXISTS a11_prompt_assignments (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    experiment_id UUID NOT NULL REFERENCES a11_prompt_experiments(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL,
    variant VARCHAR(16) NOT NULL CHECK (variant IN ('control','candidate')),
    prompt_version_id UUID NOT NULL REFERENCES a11_prompt_versions(id) ON DELETE RESTRICT,
    assignment_hash VARCHAR(64) NOT NULL,
    status VARCHAR(24) NOT NULL DEFAULT 'assigned' CHECK (status IN ('assigned','exposed','generation_failed','guard_blocked')),
    assigned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP,
    CONSTRAINT uq_a11_assignment_unit UNIQUE (empresa_id, experiment_id, conversation_id)
);
CREATE INDEX IF NOT EXISTS ix_a11_assignments_tenant_state ON a11_prompt_assignments(empresa_id, status, assigned_at);

CREATE TABLE IF NOT EXISTS a11_prompt_exposures (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    experiment_id UUID NOT NULL REFERENCES a11_prompt_experiments(id) ON DELETE CASCADE,
    assignment_id UUID NOT NULL UNIQUE REFERENCES a11_prompt_assignments(id) ON DELETE CASCADE,
    conversation_id UUID NOT NULL,
    variant VARCHAR(16) NOT NULL CHECK (variant IN ('control','candidate')),
    prompt_version_id UUID NOT NULL REFERENCES a11_prompt_versions(id) ON DELETE RESTRICT,
    assignment_hash VARCHAR(64) NOT NULL,
    exposed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    generation_latency_ms INTEGER,
    input_tokens INTEGER,
    output_tokens INTEGER,
    guard_allowed BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT uq_a11_exposure_unit UNIQUE (empresa_id, experiment_id, conversation_id)
);
CREATE INDEX IF NOT EXISTS ix_a11_exposures_tenant_time ON a11_prompt_exposures(empresa_id, exposed_at);

CREATE TABLE IF NOT EXISTS a11_prompt_evaluations (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    prompt_version_id UUID NOT NULL REFERENCES a11_prompt_versions(id) ON DELETE CASCADE,
    suite_version VARCHAR(32) NOT NULL,
    source_kind VARCHAR(24) NOT NULL DEFAULT 'synthetic' CHECK (source_kind IN ('synthetic','anonymized')),
    result_json JSONB NOT NULL,
    passed BOOLEAN NOT NULL,
    evaluated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_a11_eval_tenant_version ON a11_prompt_evaluations(empresa_id, prompt_version_id);

CREATE TABLE IF NOT EXISTS a11_prompt_audits (
    id UUID PRIMARY KEY,
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    actor_id VARCHAR(128),
    action VARCHAR(48) NOT NULL,
    object_type VARCHAR(32) NOT NULL,
    object_id UUID,
    details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS ix_a11_audit_tenant_time ON a11_prompt_audits(empresa_id, created_at);

CREATE OR REPLACE FUNCTION a11_prompt_version_immutable() RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION 'A11 prompt versions are immutable';
END;
$$ LANGUAGE plpgsql;
DROP TRIGGER IF EXISTS trg_a11_prompt_versions_immutable ON a11_prompt_versions;
CREATE TRIGGER trg_a11_prompt_versions_immutable
BEFORE UPDATE OR DELETE ON a11_prompt_versions
FOR EACH ROW EXECUTE FUNCTION a11_prompt_version_immutable();

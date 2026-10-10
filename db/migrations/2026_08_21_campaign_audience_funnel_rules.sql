CREATE TABLE IF NOT EXISTS campaign_audience_funnel_settings (
    id uuid PRIMARY KEY,
    empresa_id uuid NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    funnel_id uuid NOT NULL REFERENCES crm_funis(id) ON DELETE CASCADE,
    description text,
    status varchar NOT NULL DEFAULT 'active',
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    CONSTRAINT uq_campaign_audience_funnel_settings_empresa_funnel UNIQUE (empresa_id, funnel_id)
);

CREATE INDEX IF NOT EXISTS ix_campaign_audience_funnel_settings_lookup
    ON campaign_audience_funnel_settings (empresa_id, status, funnel_id);

CREATE TABLE IF NOT EXISTS campaign_funnel_stage_rules (
    id uuid PRIMARY KEY,
    empresa_id uuid NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    funnel_id uuid NOT NULL REFERENCES crm_funis(id) ON DELETE CASCADE,
    stage_id uuid NOT NULL REFERENCES crm_etapas(id) ON DELETE CASCADE,
    rules_json jsonb NOT NULL DEFAULT '{}'::jsonb,
    status varchar NOT NULL DEFAULT 'active',
    created_at timestamp without time zone DEFAULT now(),
    updated_at timestamp without time zone DEFAULT now(),
    CONSTRAINT uq_campaign_funnel_stage_rules_empresa_stage UNIQUE (empresa_id, stage_id)
);

CREATE INDEX IF NOT EXISTS ix_campaign_funnel_stage_rules_lookup
    ON campaign_funnel_stage_rules (empresa_id, funnel_id, status, stage_id);

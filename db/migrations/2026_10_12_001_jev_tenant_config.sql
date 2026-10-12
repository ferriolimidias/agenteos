-- A06 additive, isolated tenant configuration; no existing credentials JSON is modified.
CREATE TABLE IF NOT EXISTS jev_tenant_config (
    empresa_id UUID PRIMARY KEY REFERENCES empresas(id) ON DELETE CASCADE,
    provider TEXT NOT NULL DEFAULT 'typesafe' CHECK (provider = 'typesafe'),
    model TEXT NOT NULL DEFAULT 'jev-1.13.0' CHECK (model = 'jev-1.13.0'),
    daily_budget_usd NUMERIC(12,6) NOT NULL DEFAULT 0 CHECK (daily_budget_usd BETWEEN 0 AND 1000),
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    mode TEXT NOT NULL DEFAULT 'shadow' CHECK (mode = 'shadow'),
    timeout_seconds INTEGER NOT NULL DEFAULT 8 CHECK (timeout_seconds BETWEEN 1 AND 15),
    api_key_encrypted TEXT,
    connection_status TEXT NOT NULL DEFAULT 'not_configured' CHECK (connection_status IN (
        'not_configured','not_evaluated','connected','credential_unavailable','invalid_response',
        'invalid_credentials','rate_limited','unavailable','timeout')),
    tested_at TIMESTAMPTZ,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version > 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

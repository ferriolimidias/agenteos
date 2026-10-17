-- Central Ferrioli Meta System User credential and verified asset mappings.
-- The token is encrypted by the application before it reaches this table.
ALTER TABLE meta_marketing_tenant_account_grants
  ADD COLUMN IF NOT EXISTS write_enabled BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS meta_system_user_credentials (
  id INTEGER PRIMARY KEY CHECK (id = 1),
  encrypted_access_token TEXT NOT NULL,
  token_hint VARCHAR(16) NOT NULL,
  system_user_id VARCHAR(100),
  system_user_name VARCHAR(200),
  app_id VARCHAR(100),
  app_name VARCHAR(200),
  granted_permissions JSONB NOT NULL DEFAULT '[]'::jsonb,
  validated_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ,
  expiry_status VARCHAR(40) NOT NULL DEFAULT 'unknown',
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_by_user_id UUID REFERENCES usuarios(id) ON DELETE SET NULL
);

ALTER TABLE meta_system_user_credentials
  ADD COLUMN IF NOT EXISTS permissions_status VARCHAR(24) NOT NULL DEFAULT 'unknown',
  ADD COLUMN IF NOT EXISTS permissions_error_code VARCHAR(48),
  ADD COLUMN IF NOT EXISTS app_name VARCHAR(200),
  ADD COLUMN IF NOT EXISTS expiry_status VARCHAR(40) NOT NULL DEFAULT 'unknown';

CREATE TABLE IF NOT EXISTS meta_platform_assets (
  id UUID PRIMARY KEY,
  asset_type VARCHAR(32) NOT NULL,
  external_id VARCHAR(120) NOT NULL,
  name VARCHAR(240),
  business_id VARCHAR(120),
  business_name VARCHAR(240),
  business_access_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  access_state VARCHAR(40) NOT NULL DEFAULT 'verified',
  access_path VARCHAR(48),
  permissions_json JSONB NOT NULL DEFAULT '[]'::jsonb,
  metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_meta_platform_asset_type_external_id UNIQUE (asset_type, external_id),
  CONSTRAINT uq_meta_platform_asset_id_type UNIQUE (id, asset_type),
  CONSTRAINT ck_meta_platform_asset_type CHECK (
    asset_type IN ('business','ad_account','page','instagram_account','dataset','pixel')
  )
);

CREATE INDEX IF NOT EXISTS ix_meta_platform_assets_type_access
  ON meta_platform_assets (asset_type, access_state);

ALTER TABLE meta_platform_assets
  ADD COLUMN IF NOT EXISTS business_access_json JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS meta_tenant_asset_links (
  id UUID PRIMARY KEY,
  empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
  asset_id UUID NOT NULL,
  asset_type VARCHAR(32) NOT NULL,
  read_enabled BOOLEAN NOT NULL DEFAULT TRUE,
  write_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  verified_at TIMESTAMPTZ NOT NULL,
  linked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  linked_by_user_id UUID REFERENCES usuarios(id) ON DELETE SET NULL,
  CONSTRAINT uq_meta_tenant_asset_link_company_asset UNIQUE (empresa_id, asset_id),
  CONSTRAINT fk_meta_tenant_asset_link_asset_type
    FOREIGN KEY (asset_id, asset_type)
    REFERENCES meta_platform_assets(id, asset_type) ON DELETE CASCADE
);

ALTER TABLE meta_tenant_asset_links
  ADD COLUMN IF NOT EXISTS asset_type VARCHAR(32);

UPDATE meta_tenant_asset_links AS link
SET asset_type = asset.asset_type
FROM meta_platform_assets AS asset
WHERE link.asset_id = asset.id AND link.asset_type IS NULL;

ALTER TABLE meta_tenant_asset_links
  ALTER COLUMN asset_type SET NOT NULL;

DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'uq_meta_platform_asset_id_type'
      AND conrelid = 'public.meta_platform_assets'::regclass
  ) THEN
    ALTER TABLE meta_platform_assets
      ADD CONSTRAINT uq_meta_platform_asset_id_type UNIQUE (id, asset_type);
  END IF;
  IF EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'uq_meta_tenant_asset_link_asset'
      AND conrelid = 'public.meta_tenant_asset_links'::regclass
  ) THEN
    ALTER TABLE meta_tenant_asset_links DROP CONSTRAINT uq_meta_tenant_asset_link_asset;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'fk_meta_tenant_asset_link_asset_type'
      AND conrelid = 'public.meta_tenant_asset_links'::regclass
  ) THEN
    ALTER TABLE meta_tenant_asset_links
      ADD CONSTRAINT fk_meta_tenant_asset_link_asset_type
      FOREIGN KEY (asset_id, asset_type)
      REFERENCES meta_platform_assets(id, asset_type) ON DELETE CASCADE;
  END IF;
END $$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_meta_tenant_asset_link_exclusive_asset
  ON meta_tenant_asset_links (asset_id)
  WHERE asset_type <> 'business';

CREATE INDEX IF NOT EXISTS ix_meta_tenant_asset_links_empresa
  ON meta_tenant_asset_links (empresa_id);

CREATE TABLE IF NOT EXISTS meta_system_user_audit (
  id UUID PRIMARY KEY,
  actor_user_id UUID REFERENCES usuarios(id) ON DELETE SET NULL,
  empresa_id UUID REFERENCES empresas(id) ON DELETE SET NULL,
  action VARCHAR(40) NOT NULL,
  details_json JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT ck_meta_system_user_audit_action CHECK (
    action IN ('credential_saved','credential_revoked','assets_discovered','asset_id_validated','tenant_assets_linked')
  )
);

CREATE INDEX IF NOT EXISTS ix_meta_system_user_audit_created_at
  ON meta_system_user_audit (created_at);

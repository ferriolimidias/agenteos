DO $$
BEGIN
  IF to_regclass('public.traffic_meta_configs') IS NULL THEN
    RAISE EXCEPTION 'Meta Marketing account grants require traffic_meta_configs';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'uq_traffic_meta_configs_empresa_ad_account'
      AND conrelid = 'public.traffic_meta_configs'::regclass
  ) THEN
    ALTER TABLE traffic_meta_configs
      ADD CONSTRAINT uq_traffic_meta_configs_empresa_ad_account UNIQUE (empresa_id, ad_account_id);
  END IF;
END $$;

CREATE TABLE IF NOT EXISTS meta_marketing_tenant_account_grants (
  id UUID PRIMARY KEY,
  empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
  business_id VARCHAR(100) NOT NULL,
  ad_account_id VARCHAR(100) NOT NULL,
  credential_source VARCHAR(48) NOT NULL DEFAULT 'ADMIN_MANAGED_MARKETING_TOKEN',
  read_enabled BOOLEAN NOT NULL DEFAULT FALSE,
  verification_method VARCHAR(40) NOT NULL,
  verified_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT uq_meta_marketing_grant_empresa_account UNIQUE (empresa_id, ad_account_id),
  CONSTRAINT fk_meta_marketing_grant_tenant_account
    FOREIGN KEY (empresa_id, ad_account_id)
    REFERENCES traffic_meta_configs(empresa_id, ad_account_id) ON DELETE CASCADE,
  CONSTRAINT ck_meta_marketing_grant_credential_source
    CHECK (credential_source = 'ADMIN_MANAGED_MARKETING_TOKEN'),
  CONSTRAINT ck_meta_marketing_grant_verification_method
    CHECK (verification_method IN ('business_owner', 'delegated_client_ad_account')),
  CONSTRAINT ck_meta_marketing_grant_business_id CHECK (length(trim(business_id)) > 0),
  CONSTRAINT ck_meta_marketing_grant_ad_account_id CHECK (length(trim(ad_account_id)) > 0)
);

CREATE INDEX IF NOT EXISTS ix_meta_marketing_grants_empresa_enabled
  ON meta_marketing_tenant_account_grants (empresa_id, read_enabled);

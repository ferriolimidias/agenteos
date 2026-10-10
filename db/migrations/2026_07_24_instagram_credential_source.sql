ALTER TABLE conexoes ADD COLUMN IF NOT EXISTS credential_source VARCHAR NOT NULL DEFAULT 'tenant_connection';
UPDATE conexoes
SET credential_source = 'tenant_connection'
WHERE credential_source IS NULL OR btrim(credential_source) = '';
ALTER TABLE conexoes ALTER COLUMN credential_source SET DEFAULT 'tenant_connection';

-- Optional tenant-level OpenAI reasoning effort. Existing rows remain NULL,
-- which is interpreted as "default" and omits reasoning_effort at runtime.
ALTER TABLE empresas
    ADD COLUMN IF NOT EXISTS modelo_ia_reasoning_effort VARCHAR(16);

-- ``create_all`` can create the new ORM column before this migration runs.
-- Add the constraint independently so that path receives the same validation.
DO $migration$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_constraint
        WHERE conrelid = 'empresas'::regclass
          AND conname = 'ck_empresas_modelo_ia_reasoning_effort'
    ) THEN
        ALTER TABLE empresas
            ADD CONSTRAINT ck_empresas_modelo_ia_reasoning_effort
            CHECK (
                modelo_ia_reasoning_effort IS NULL
                OR modelo_ia_reasoning_effort IN ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
            );
    END IF;
END
$migration$;

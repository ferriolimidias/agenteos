-- Fase 2: persistencia multicanal tenant-safe.
-- Aplicar em transacao unica quando executado via psql: psql -v ON_ERROR_STOP=1 -1 -f este_arquivo.sql
-- Nao aplicar no banco real sem gate humano, backup validado e prechecks aprovados.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

DO $$
BEGIN
    IF to_regclass('public.empresas') IS NULL THEN
        RAISE EXCEPTION 'precheck failed: tabela empresas ausente';
    END IF;
    IF to_regclass('public.conexoes') IS NULL THEN
        RAISE EXCEPTION 'precheck failed: tabela conexoes ausente';
    END IF;
    IF to_regclass('public.crm_leads') IS NULL THEN
        RAISE EXCEPTION 'precheck failed: tabela crm_leads ausente';
    END IF;
    IF to_regclass('public.mensagens_historico') IS NULL THEN
        RAISE EXCEPTION 'precheck failed: tabela mensagens_historico ausente';
    END IF;
END $$;

ALTER TABLE conexoes ADD COLUMN IF NOT EXISTS channel VARCHAR NULL;
ALTER TABLE conexoes ADD COLUMN IF NOT EXISTS provider VARCHAR NULL;
ALTER TABLE conexoes ADD COLUMN IF NOT EXISTS is_default BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE conexoes ADD COLUMN IF NOT EXISTS external_account_id VARCHAR NULL;
ALTER TABLE conexoes ADD COLUMN IF NOT EXISTS waba_id VARCHAR NULL;
ALTER TABLE conexoes ADD COLUMN IF NOT EXISTS phone_number_id VARCHAR NULL;
ALTER TABLE conexoes ADD COLUMN IF NOT EXISTS instagram_business_account_id VARCHAR NULL;
ALTER TABLE conexoes ADD COLUMN IF NOT EXISTS page_id VARCHAR NULL;
ALTER TABLE conexoes ADD COLUMN IF NOT EXISTS display_number VARCHAR NULL;
ALTER TABLE conexoes ADD COLUMN IF NOT EXISTS metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE conexoes ADD COLUMN IF NOT EXISTS atualizado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT now();

UPDATE conexoes
SET
    channel = COALESCE(channel, CASE
        WHEN tipo::text = 'instagram' THEN 'instagram'
        ELSE 'whatsapp'
    END),
    provider = COALESCE(provider, CASE
        WHEN tipo::text = 'evolution' THEN 'evolution_legacy'
        WHEN tipo::text = 'meta' THEN 'meta_whatsapp'
        WHEN tipo::text = 'instagram' THEN 'meta_instagram'
        ELSE tipo::text
    END)
WHERE channel IS NULL OR provider IS NULL;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM conexoes WHERE empresa_id IS NULL OR id IS NULL) THEN
        RAISE EXCEPTION 'precheck failed: conexoes possui empresa_id/id nulo';
    END IF;
    IF EXISTS (SELECT 1 FROM crm_leads WHERE empresa_id IS NULL OR id IS NULL) THEN
        RAISE EXCEPTION 'precheck failed: crm_leads possui empresa_id/id nulo';
    END IF;
    IF EXISTS (
        SELECT 1
        FROM mensagens_historico mh
        LEFT JOIN crm_leads cl ON cl.id = mh.lead_id
        WHERE cl.id IS NULL
    ) THEN
        RAISE EXCEPTION 'precheck failed: mensagens_historico possui lead_id sem crm_leads correspondente';
    END IF;
END $$;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.conexoes'::regclass
          AND conname = 'uq_conexoes_empresa_id_id'
    ) THEN
        ALTER TABLE conexoes ADD CONSTRAINT uq_conexoes_empresa_id_id UNIQUE (empresa_id, id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.crm_leads'::regclass
          AND conname = 'uq_crm_leads_empresa_id_id'
    ) THEN
        ALTER TABLE crm_leads ADD CONSTRAINT uq_crm_leads_empresa_id_id UNIQUE (empresa_id, id);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS channel_contacts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    conexao_id UUID NOT NULL REFERENCES conexoes(id) ON DELETE CASCADE,
    lead_id UUID NULL REFERENCES crm_leads(id) ON DELETE SET NULL,
    channel VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    external_contact_id VARCHAR NOT NULL,
    normalized_address VARCHAR NULL,
    phone_e164 VARCHAR NULL,
    username VARCHAR NULL,
    display_name VARCHAR NULL,
    profile_photo_url TEXT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    first_seen_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    last_seen_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    criado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    atualizado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    CONSTRAINT uq_channel_contacts_identity UNIQUE (
        empresa_id, channel, provider, conexao_id, external_contact_id
    )
);

ALTER TABLE channel_contacts ADD COLUMN IF NOT EXISTS id UUID DEFAULT gen_random_uuid();
ALTER TABLE channel_contacts ADD COLUMN IF NOT EXISTS empresa_id UUID;
ALTER TABLE channel_contacts ADD COLUMN IF NOT EXISTS conexao_id UUID;
ALTER TABLE channel_contacts ADD COLUMN IF NOT EXISTS lead_id UUID;
ALTER TABLE channel_contacts ADD COLUMN IF NOT EXISTS channel VARCHAR;
ALTER TABLE channel_contacts ADD COLUMN IF NOT EXISTS provider VARCHAR;
ALTER TABLE channel_contacts ADD COLUMN IF NOT EXISTS external_contact_id VARCHAR;
ALTER TABLE channel_contacts ADD COLUMN IF NOT EXISTS normalized_address VARCHAR;
ALTER TABLE channel_contacts ADD COLUMN IF NOT EXISTS phone_e164 VARCHAR;
ALTER TABLE channel_contacts ADD COLUMN IF NOT EXISTS username VARCHAR;
ALTER TABLE channel_contacts ADD COLUMN IF NOT EXISTS display_name VARCHAR;
ALTER TABLE channel_contacts ADD COLUMN IF NOT EXISTS profile_photo_url TEXT;
ALTER TABLE channel_contacts ADD COLUMN IF NOT EXISTS metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE channel_contacts ADD COLUMN IF NOT EXISTS first_seen_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now();
ALTER TABLE channel_contacts ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMP WITHOUT TIME ZONE DEFAULT now();
ALTER TABLE channel_contacts ADD COLUMN IF NOT EXISTS criado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT now();
ALTER TABLE channel_contacts ADD COLUMN IF NOT EXISTS atualizado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT now();

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM channel_contacts
        WHERE id IS NULL OR empresa_id IS NULL OR conexao_id IS NULL
           OR channel IS NULL OR provider IS NULL OR external_contact_id IS NULL
    ) THEN
        RAISE EXCEPTION 'precheck failed: channel_contacts parcial contem nulos em colunas obrigatorias';
    END IF;
END $$;

ALTER TABLE channel_contacts ALTER COLUMN id SET NOT NULL;
ALTER TABLE channel_contacts ALTER COLUMN empresa_id SET NOT NULL;
ALTER TABLE channel_contacts ALTER COLUMN conexao_id SET NOT NULL;
ALTER TABLE channel_contacts ALTER COLUMN channel SET NOT NULL;
ALTER TABLE channel_contacts ALTER COLUMN provider SET NOT NULL;
ALTER TABLE channel_contacts ALTER COLUMN external_contact_id SET NOT NULL;
ALTER TABLE channel_contacts ALTER COLUMN metadata_json SET DEFAULT '{}'::jsonb;
ALTER TABLE channel_contacts ALTER COLUMN metadata_json SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.channel_contacts'::regclass
          AND contype = 'p'
    ) THEN
        ALTER TABLE channel_contacts ADD CONSTRAINT channel_contacts_pkey PRIMARY KEY (id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.channel_contacts'::regclass
          AND conname = 'uq_channel_contacts_identity'
    ) THEN
        ALTER TABLE channel_contacts
        ADD CONSTRAINT uq_channel_contacts_identity
        UNIQUE (empresa_id, channel, provider, conexao_id, external_contact_id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.channel_contacts'::regclass
          AND conname = 'uq_channel_contacts_empresa_id_id'
    ) THEN
        ALTER TABLE channel_contacts
        ADD CONSTRAINT uq_channel_contacts_empresa_id_id UNIQUE (empresa_id, id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.channel_contacts'::regclass
          AND conname = 'channel_contacts_empresa_id_fkey'
    ) THEN
        ALTER TABLE channel_contacts
        ADD CONSTRAINT channel_contacts_empresa_id_fkey
        FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.channel_contacts'::regclass
          AND conname = 'channel_contacts_conexao_id_fkey'
    ) THEN
        ALTER TABLE channel_contacts
        ADD CONSTRAINT channel_contacts_conexao_id_fkey
        FOREIGN KEY (conexao_id) REFERENCES conexoes(id) ON DELETE CASCADE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.channel_contacts'::regclass
          AND conname = 'channel_contacts_lead_id_fkey'
    ) THEN
        ALTER TABLE channel_contacts
        ADD CONSTRAINT channel_contacts_lead_id_fkey
        FOREIGN KEY (lead_id) REFERENCES crm_leads(id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.channel_contacts'::regclass
          AND conname = 'fk_channel_contacts_empresa_conexao'
    ) THEN
        ALTER TABLE channel_contacts
        ADD CONSTRAINT fk_channel_contacts_empresa_conexao
        FOREIGN KEY (empresa_id, conexao_id) REFERENCES conexoes (empresa_id, id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.channel_contacts'::regclass
          AND conname = 'fk_channel_contacts_empresa_lead'
    ) THEN
        ALTER TABLE channel_contacts
        ADD CONSTRAINT fk_channel_contacts_empresa_lead
        FOREIGN KEY (empresa_id, lead_id) REFERENCES crm_leads (empresa_id, id);
    END IF;
END $$;

CREATE TABLE IF NOT EXISTS channel_conversations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    empresa_id UUID NOT NULL REFERENCES empresas(id) ON DELETE CASCADE,
    conexao_id UUID NOT NULL REFERENCES conexoes(id) ON DELETE CASCADE,
    contact_id UUID NOT NULL REFERENCES channel_contacts(id) ON DELETE CASCADE,
    lead_id UUID NULL REFERENCES crm_leads(id) ON DELETE SET NULL,
    channel VARCHAR NOT NULL,
    provider VARCHAR NOT NULL,
    conversation_key VARCHAR NOT NULL,
    external_conversation_id VARCHAR NOT NULL,
    external_thread_id VARCHAR NULL,
    status VARCHAR NOT NULL DEFAULT 'open',
    last_message_at TIMESTAMP WITHOUT TIME ZONE NULL,
    last_inbound_at TIMESTAMP WITHOUT TIME ZONE NULL,
    last_outbound_at TIMESTAMP WITHOUT TIME ZONE NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    criado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    atualizado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT now(),
    CONSTRAINT uq_channel_conversations_identity UNIQUE (
        empresa_id, channel, provider, conexao_id, conversation_key
    )
);

ALTER TABLE channel_conversations ADD COLUMN IF NOT EXISTS id UUID DEFAULT gen_random_uuid();
ALTER TABLE channel_conversations ADD COLUMN IF NOT EXISTS empresa_id UUID;
ALTER TABLE channel_conversations ADD COLUMN IF NOT EXISTS conexao_id UUID;
ALTER TABLE channel_conversations ADD COLUMN IF NOT EXISTS contact_id UUID;
ALTER TABLE channel_conversations ADD COLUMN IF NOT EXISTS lead_id UUID;
ALTER TABLE channel_conversations ADD COLUMN IF NOT EXISTS channel VARCHAR;
ALTER TABLE channel_conversations ADD COLUMN IF NOT EXISTS provider VARCHAR;
ALTER TABLE channel_conversations ADD COLUMN IF NOT EXISTS conversation_key VARCHAR;
ALTER TABLE channel_conversations ADD COLUMN IF NOT EXISTS external_conversation_id VARCHAR;
ALTER TABLE channel_conversations ADD COLUMN IF NOT EXISTS external_thread_id VARCHAR;
ALTER TABLE channel_conversations ADD COLUMN IF NOT EXISTS status VARCHAR DEFAULT 'open';
ALTER TABLE channel_conversations ADD COLUMN IF NOT EXISTS last_message_at TIMESTAMP WITHOUT TIME ZONE;
ALTER TABLE channel_conversations ADD COLUMN IF NOT EXISTS last_inbound_at TIMESTAMP WITHOUT TIME ZONE;
ALTER TABLE channel_conversations ADD COLUMN IF NOT EXISTS last_outbound_at TIMESTAMP WITHOUT TIME ZONE;
ALTER TABLE channel_conversations ADD COLUMN IF NOT EXISTS metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb;
ALTER TABLE channel_conversations ADD COLUMN IF NOT EXISTS criado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT now();
ALTER TABLE channel_conversations ADD COLUMN IF NOT EXISTS atualizado_em TIMESTAMP WITHOUT TIME ZONE DEFAULT now();

DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM channel_conversations
        WHERE id IS NULL OR empresa_id IS NULL OR conexao_id IS NULL OR contact_id IS NULL
           OR channel IS NULL OR provider IS NULL OR conversation_key IS NULL
           OR external_conversation_id IS NULL OR status IS NULL
    ) THEN
        RAISE EXCEPTION 'precheck failed: channel_conversations parcial contem nulos em colunas obrigatorias';
    END IF;
END $$;

ALTER TABLE channel_conversations ALTER COLUMN id SET NOT NULL;
ALTER TABLE channel_conversations ALTER COLUMN empresa_id SET NOT NULL;
ALTER TABLE channel_conversations ALTER COLUMN conexao_id SET NOT NULL;
ALTER TABLE channel_conversations ALTER COLUMN contact_id SET NOT NULL;
ALTER TABLE channel_conversations ALTER COLUMN channel SET NOT NULL;
ALTER TABLE channel_conversations ALTER COLUMN provider SET NOT NULL;
ALTER TABLE channel_conversations ALTER COLUMN conversation_key SET NOT NULL;
ALTER TABLE channel_conversations ALTER COLUMN external_conversation_id SET NOT NULL;
ALTER TABLE channel_conversations ALTER COLUMN status SET DEFAULT 'open';
ALTER TABLE channel_conversations ALTER COLUMN status SET NOT NULL;
ALTER TABLE channel_conversations ALTER COLUMN metadata_json SET DEFAULT '{}'::jsonb;
ALTER TABLE channel_conversations ALTER COLUMN metadata_json SET NOT NULL;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.channel_conversations'::regclass
          AND contype = 'p'
    ) THEN
        ALTER TABLE channel_conversations ADD CONSTRAINT channel_conversations_pkey PRIMARY KEY (id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.channel_conversations'::regclass
          AND conname = 'uq_channel_conversations_identity'
    ) THEN
        ALTER TABLE channel_conversations
        ADD CONSTRAINT uq_channel_conversations_identity
        UNIQUE (empresa_id, channel, provider, conexao_id, conversation_key);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.channel_conversations'::regclass
          AND conname = 'uq_channel_conversations_empresa_id_id'
    ) THEN
        ALTER TABLE channel_conversations
        ADD CONSTRAINT uq_channel_conversations_empresa_id_id UNIQUE (empresa_id, id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.channel_conversations'::regclass
          AND conname = 'channel_conversations_empresa_id_fkey'
    ) THEN
        ALTER TABLE channel_conversations
        ADD CONSTRAINT channel_conversations_empresa_id_fkey
        FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.channel_conversations'::regclass
          AND conname = 'channel_conversations_conexao_id_fkey'
    ) THEN
        ALTER TABLE channel_conversations
        ADD CONSTRAINT channel_conversations_conexao_id_fkey
        FOREIGN KEY (conexao_id) REFERENCES conexoes(id) ON DELETE CASCADE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.channel_conversations'::regclass
          AND conname = 'channel_conversations_contact_id_fkey'
    ) THEN
        ALTER TABLE channel_conversations
        ADD CONSTRAINT channel_conversations_contact_id_fkey
        FOREIGN KEY (contact_id) REFERENCES channel_contacts(id) ON DELETE CASCADE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.channel_conversations'::regclass
          AND conname = 'channel_conversations_lead_id_fkey'
    ) THEN
        ALTER TABLE channel_conversations
        ADD CONSTRAINT channel_conversations_lead_id_fkey
        FOREIGN KEY (lead_id) REFERENCES crm_leads(id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.channel_conversations'::regclass
          AND conname = 'fk_channel_conversations_empresa_conexao'
    ) THEN
        ALTER TABLE channel_conversations
        ADD CONSTRAINT fk_channel_conversations_empresa_conexao
        FOREIGN KEY (empresa_id, conexao_id) REFERENCES conexoes (empresa_id, id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.channel_conversations'::regclass
          AND conname = 'fk_channel_conversations_empresa_contact'
    ) THEN
        ALTER TABLE channel_conversations
        ADD CONSTRAINT fk_channel_conversations_empresa_contact
        FOREIGN KEY (empresa_id, contact_id) REFERENCES channel_contacts (empresa_id, id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.channel_conversations'::regclass
          AND conname = 'fk_channel_conversations_empresa_lead'
    ) THEN
        ALTER TABLE channel_conversations
        ADD CONSTRAINT fk_channel_conversations_empresa_lead
        FOREIGN KEY (empresa_id, lead_id) REFERENCES crm_leads (empresa_id, id);
    END IF;
END $$;

ALTER TABLE mensagens_historico ADD COLUMN IF NOT EXISTS empresa_id UUID NULL;
UPDATE mensagens_historico mh
SET empresa_id = cl.empresa_id
FROM crm_leads cl
WHERE mh.lead_id = cl.id
  AND mh.empresa_id IS NULL;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM mensagens_historico WHERE empresa_id IS NULL) THEN
        RAISE EXCEPTION 'precheck failed: nao foi possivel preencher mensagens_historico.empresa_id a partir de crm_leads';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM mensagens_historico mh
        JOIN crm_leads cl ON cl.id = mh.lead_id
        WHERE mh.empresa_id IS DISTINCT FROM cl.empresa_id
    ) THEN
        RAISE EXCEPTION 'precheck failed: mensagens_historico possui empresa_id divergente do lead';
    END IF;
END $$;

ALTER TABLE mensagens_historico ALTER COLUMN empresa_id SET NOT NULL;
ALTER TABLE mensagens_historico ADD COLUMN IF NOT EXISTS channel_conversation_id UUID NULL;
ALTER TABLE mensagens_historico ADD COLUMN IF NOT EXISTS channel VARCHAR NULL;
ALTER TABLE mensagens_historico ADD COLUMN IF NOT EXISTS provider VARCHAR NULL;
ALTER TABLE mensagens_historico ADD COLUMN IF NOT EXISTS external_message_id VARCHAR NULL;
ALTER TABLE mensagens_historico ADD COLUMN IF NOT EXISTS external_conversation_id VARCHAR NULL;
ALTER TABLE mensagens_historico ADD COLUMN IF NOT EXISTS direction VARCHAR NULL;
ALTER TABLE mensagens_historico ADD COLUMN IF NOT EXISTS message_status VARCHAR NULL;
ALTER TABLE mensagens_historico ADD COLUMN IF NOT EXISTS provider_status VARCHAR NULL;
ALTER TABLE mensagens_historico ADD COLUMN IF NOT EXISTS metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.mensagens_historico'::regclass
          AND conname = 'mensagens_historico_empresa_id_fkey'
    ) THEN
        ALTER TABLE mensagens_historico
        ADD CONSTRAINT mensagens_historico_empresa_id_fkey
        FOREIGN KEY (empresa_id) REFERENCES empresas(id) ON DELETE CASCADE;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.mensagens_historico'::regclass
          AND conname = 'mensagens_historico_channel_conversation_id_fkey'
    ) THEN
        ALTER TABLE mensagens_historico
        ADD CONSTRAINT mensagens_historico_channel_conversation_id_fkey
        FOREIGN KEY (channel_conversation_id) REFERENCES channel_conversations(id) ON DELETE SET NULL;
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.mensagens_historico'::regclass
          AND conname = 'fk_mensagens_historico_empresa_lead'
    ) THEN
        ALTER TABLE mensagens_historico
        ADD CONSTRAINT fk_mensagens_historico_empresa_lead
        FOREIGN KEY (empresa_id, lead_id) REFERENCES crm_leads (empresa_id, id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.mensagens_historico'::regclass
          AND conname = 'fk_mensagens_historico_empresa_conexao'
    ) THEN
        ALTER TABLE mensagens_historico
        ADD CONSTRAINT fk_mensagens_historico_empresa_conexao
        FOREIGN KEY (empresa_id, conexao_id) REFERENCES conexoes (empresa_id, id);
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conrelid = 'public.mensagens_historico'::regclass
          AND conname = 'fk_mensagens_historico_empresa_conversation'
    ) THEN
        ALTER TABLE mensagens_historico
        ADD CONSTRAINT fk_mensagens_historico_empresa_conversation
        FOREIGN KEY (empresa_id, channel_conversation_id) REFERENCES channel_conversations (empresa_id, id);
    END IF;
END $$;

CREATE INDEX IF NOT EXISTS ix_conexoes_multichannel_lookup ON conexoes (empresa_id, channel, provider, status);
CREATE INDEX IF NOT EXISTS ix_channel_contacts_lead ON channel_contacts (empresa_id, lead_id);
CREATE INDEX IF NOT EXISTS ix_channel_contacts_normalized_address ON channel_contacts (empresa_id, normalized_address);
CREATE INDEX IF NOT EXISTS ix_channel_conversations_lead ON channel_conversations (empresa_id, lead_id);
CREATE INDEX IF NOT EXISTS ix_channel_conversations_contact ON channel_conversations (empresa_id, contact_id);
CREATE INDEX IF NOT EXISTS ix_mensagens_historico_channel_conversation ON mensagens_historico (channel_conversation_id);
CREATE INDEX IF NOT EXISTS ix_mensagens_historico_external_message ON mensagens_historico (empresa_id, external_message_id);

DO $$
BEGIN
    IF EXISTS (
        SELECT 1
        FROM channel_contacts cc
        JOIN conexoes c ON c.id = cc.conexao_id
        WHERE c.empresa_id IS DISTINCT FROM cc.empresa_id
    ) THEN
        RAISE EXCEPTION 'postcheck failed: channel_contacts referencia conexao de outro tenant';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM channel_contacts cc
        JOIN crm_leads cl ON cl.id = cc.lead_id
        WHERE cc.lead_id IS NOT NULL
          AND cl.empresa_id IS DISTINCT FROM cc.empresa_id
    ) THEN
        RAISE EXCEPTION 'postcheck failed: channel_contacts referencia lead de outro tenant';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM channel_conversations cv
        JOIN channel_contacts cc ON cc.id = cv.contact_id
        WHERE cc.empresa_id IS DISTINCT FROM cv.empresa_id
    ) THEN
        RAISE EXCEPTION 'postcheck failed: channel_conversations referencia contato de outro tenant';
    END IF;

    IF EXISTS (
        SELECT 1
        FROM mensagens_historico mh
        JOIN crm_leads cl ON cl.id = mh.lead_id
        WHERE cl.empresa_id IS DISTINCT FROM mh.empresa_id
    ) THEN
        RAISE EXCEPTION 'postcheck failed: mensagens_historico referencia lead de outro tenant';
    END IF;
END $$;

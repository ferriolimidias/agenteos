BEGIN;

CREATE TABLE IF NOT EXISTS leoft_products (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id uuid NOT NULL REFERENCES empresas(id) ON DELETE RESTRICT,
  product_id text NOT NULL,
  name text NOT NULL,
  description text NULL,
  product_type text NOT NULL,
  status text NOT NULL DEFAULT 'draft',
  default_price numeric(18,6) NULL,
  currency varchar(3) NULL,
  access_policy text NULL,
  requires_anamnesis boolean NOT NULL DEFAULT false,
  requires_human_approval boolean NOT NULL DEFAULT false,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_leoft_products_company_product UNIQUE (company_id, product_id),
  CONSTRAINT ck_leoft_products_type CHECK (product_type IN ('APP_ACCESS','STANDARD_PROGRAM','PERSONALIZED_PROGRAM','COACHING')),
  CONSTRAINT ck_leoft_products_status CHECK (status IN ('draft','active','archived','legacy','test')),
  CONSTRAINT ck_leoft_products_currency CHECK (currency IS NULL OR char_length(currency) = 3)
);

CREATE TABLE IF NOT EXISTS leoft_offers (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id uuid NOT NULL REFERENCES empresas(id) ON DELETE RESTRICT,
  offer_id text NOT NULL,
  product_ref uuid NOT NULL REFERENCES leoft_products(id) ON DELETE RESTRICT,
  name text NOT NULL,
  status text NOT NULL DEFAULT 'draft',
  price numeric(18,6) NULL,
  currency varchar(3) NULL,
  checkout_provider text NULL,
  checkout_reference text NULL,
  cta text NULL,
  flow_reference text NULL,
  agent_reference text NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_leoft_offers_company_offer UNIQUE (company_id, offer_id),
  CONSTRAINT ck_leoft_offers_status CHECK (status IN ('draft','active','archived','legacy','test')),
  CONSTRAINT ck_leoft_offers_currency CHECK (currency IS NULL OR char_length(currency) = 3)
);

ALTER TABLE leoft_offer_fulfillments
  ADD COLUMN IF NOT EXISTS canonical_offer_id text NULL;
CREATE INDEX IF NOT EXISTS ix_leoft_offer_fulfillments_canonical_offer
  ON leoft_offer_fulfillments(company_id, canonical_offer_id);

CREATE TABLE IF NOT EXISTS leoft_personalization_requests (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id uuid NOT NULL REFERENCES empresas(id) ON DELETE RESTRICT,
  sale_fulfillment_id uuid NOT NULL REFERENCES leoft_sale_fulfillments(id) ON DELETE CASCADE,
  student_id uuid NOT NULL REFERENCES leoft_students(id) ON DELETE RESTRICT,
  anamnesis_id text NULL,
  analysis_id text NULL,
  status text NOT NULL DEFAULT 'pending',
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz NULL,
  CONSTRAINT uq_leoft_personalization_request_sale UNIQUE (sale_fulfillment_id),
  CONSTRAINT ck_leoft_personalization_request_status CHECK (status IN ('pending','waiting_for_anamnesis','ready_for_creation','in_progress','review_required','approved','assigned','completed','failed'))
);

ALTER TABLE leoft_sale_fulfillments
  ADD COLUMN IF NOT EXISTS personalization_request_id uuid NULL;
CREATE INDEX IF NOT EXISTS ix_leoft_sale_fulfillments_personalization
  ON leoft_sale_fulfillments(personalization_request_id);

ALTER TABLE leoft_sale_fulfillments
  DROP CONSTRAINT IF EXISTS ck_leoft_sale_fulfillment_stage;
ALTER TABLE leoft_sale_fulfillments
  ADD CONSTRAINT ck_leoft_sale_fulfillment_stage CHECK (current_stage IN ('received','sale_registered','contact_resolved','student_resolved','access_provisioned','waiting_for_anamnesis','personalization_requested','program_assigned','crm_updated','post_sale_triggered','completed','manual_review'));

ALTER TABLE leoft_sale_fulfillments
  DROP CONSTRAINT IF EXISTS ck_leoft_sale_fulfillment_post_sale;
ALTER TABLE leoft_sale_fulfillments
  ADD CONSTRAINT ck_leoft_sale_fulfillment_post_sale CHECK (post_sale_status IN ('pending','queued','skipped','failed','sent','manual_review','pending_personalization'));

-- Register only the already active offer. This preserves its recorded price and
-- copy while making its technical product/fulfillment classification explicit.
INSERT INTO leoft_products (company_id, product_id, name, description, product_type, status, default_price, currency, access_policy, requires_anamnesis, requires_human_approval, metadata)
SELECT e.id,
       'leoft_metodo_hibrido',
       COALESCE(e.credenciais_canais #>> '{ofertas,leoft_metodo_hibrido,nome}', 'Método Híbrido LeoFT'),
       COALESCE(e.credenciais_canais #>> '{metodo_hibrido_leoft_app,descricao}', 'Plano personalizado baseado em avaliação online.'),
       'PERSONALIZED_PROGRAM',
       CASE WHEN COALESCE((e.credenciais_canais #>> '{ofertas,leoft_metodo_hibrido,ativo}')::boolean, false) THEN 'active' ELSE 'legacy' END,
       CASE WHEN (e.credenciais_canais #>> '{ofertas,leoft_metodo_hibrido,valor}') ~ '^[0-9]+([.][0-9]+)?$' THEN (e.credenciais_canais #>> '{ofertas,leoft_metodo_hibrido,valor}')::numeric ELSE NULL END,
       COALESCE(e.credenciais_canais #>> '{ofertas,leoft_metodo_hibrido,moeda}', 'BRL'),
       'leo_app', true, true,
       jsonb_build_object('source', 'empresas.credenciais_canais', 'legacy_type', 'plano_treino_personalizado')
FROM empresas e
WHERE e.credenciais_canais #>> '{ofertas,leoft_metodo_hibrido,nome}' IS NOT NULL
ON CONFLICT (company_id, product_id) DO UPDATE SET
  name = EXCLUDED.name,
  description = EXCLUDED.description,
  product_type = EXCLUDED.product_type,
  status = EXCLUDED.status,
  default_price = EXCLUDED.default_price,
  currency = EXCLUDED.currency,
  requires_anamnesis = EXCLUDED.requires_anamnesis,
  requires_human_approval = EXCLUDED.requires_human_approval,
  updated_at = now();

INSERT INTO leoft_offers (company_id, offer_id, product_ref, name, status, price, currency, checkout_provider, checkout_reference, cta, flow_reference, agent_reference, metadata)
SELECT p.company_id, 'leoft_metodo_hibrido', p.id, p.name, p.status, p.default_price, p.currency,
       'mercado_pago', 'anamnese_leoft', 'QUERO COMEÇAR', 'sequencias_whatsapp.leoft_metodo_hibrido', 'Leonardo Personal',
       jsonb_build_object('source', 'empresas.credenciais_canais', 'checkout_no_final_da_anamnese', true)
FROM leoft_products p
WHERE p.product_id = 'leoft_metodo_hibrido'
ON CONFLICT (company_id, offer_id) DO UPDATE SET
  product_ref = EXCLUDED.product_ref,
  name = EXCLUDED.name,
  status = EXCLUDED.status,
  price = EXCLUDED.price,
  currency = EXCLUDED.currency,
  checkout_provider = EXCLUDED.checkout_provider,
  checkout_reference = EXCLUDED.checkout_reference,
  cta = EXCLUDED.cta,
  flow_reference = EXCLUDED.flow_reference,
  agent_reference = EXCLUDED.agent_reference,
  updated_at = now();

INSERT INTO leoft_offer_fulfillments (company_id, product_id, offer_code, product_type, fulfillment_type, training_program_template_id, active, canonical_offer_id, metadata)
SELECT p.company_id, p.product_id, 'leoft_metodo_hibrido', 'PERSONALIZED_PROGRAM', 'PERSONALIZED_PROGRAM_REQUEST', NULL, (p.status = 'active'), 'leoft_metodo_hibrido',
       jsonb_build_object('requires_anamnesis', true, 'requires_personalization_request', true, 'requires_template', false)
FROM leoft_products p
WHERE p.product_id = 'leoft_metodo_hibrido'
ON CONFLICT (company_id, product_id) DO UPDATE SET
  offer_code = EXCLUDED.offer_code,
  product_type = EXCLUDED.product_type,
  fulfillment_type = EXCLUDED.fulfillment_type,
  active = EXCLUDED.active,
  canonical_offer_id = EXCLUDED.canonical_offer_id,
  metadata = EXCLUDED.metadata,
  updated_at = now();

COMMIT;

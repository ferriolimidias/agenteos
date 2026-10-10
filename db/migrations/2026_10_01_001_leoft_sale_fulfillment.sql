BEGIN;

CREATE TABLE IF NOT EXISTS leoft_offer_fulfillments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  company_id uuid NOT NULL REFERENCES empresas(id) ON DELETE RESTRICT,
  product_id text NOT NULL,
  offer_code text NOT NULL,
  product_type text NOT NULL DEFAULT 'PERSONALIZED_PROGRAM',
  fulfillment_type text NOT NULL DEFAULT 'PROGRAM_AND_APP',
  training_program_template_id uuid NULL,
  active boolean NOT NULL DEFAULT true,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  CONSTRAINT uq_leoft_offer_fulfillment_product UNIQUE (company_id, product_id),
  CONSTRAINT ck_leoft_offer_fulfillment_product_type CHECK (btrim(product_type) <> ''),
  CONSTRAINT ck_leoft_offer_fulfillment_type CHECK (btrim(fulfillment_type) <> '')
);

CREATE TABLE IF NOT EXISTS leoft_sale_fulfillments (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  event_id uuid NOT NULL DEFAULT gen_random_uuid(),
  event_type text NOT NULL DEFAULT 'SALE_CONFIRMED',
  occurred_at timestamptz NOT NULL,
  company_id uuid NOT NULL REFERENCES empresas(id) ON DELETE RESTRICT,
  sale_id text NOT NULL,
  order_id text NOT NULL,
  payment_id text NOT NULL,
  payment_provider text NOT NULL,
  payment_status text NOT NULL,
  paid_at timestamptz NULL,
  product_id text NOT NULL,
  offer_code text NULL,
  product_type text NOT NULL,
  fulfillment_type text NOT NULL,
  amount numeric(18,6) NULL,
  currency varchar(3) NULL,
  lead_id uuid NULL,
  contact_id uuid NULL,
  student_id uuid NULL,
  access_id uuid NULL,
  anamnesis_id text NULL,
  source text NULL,
  campaign text NULL,
  funnel text NULL,
  training_program_template_id uuid NULL,
  student_program_id uuid NULL,
  status text NOT NULL DEFAULT 'received',
  current_stage text NOT NULL DEFAULT 'received',
  post_sale_status text NOT NULL DEFAULT 'pending',
  retry_count integer NOT NULL DEFAULT 0,
  next_retry_at timestamptz NULL,
  last_error_code text NULL,
  last_error_sanitized text NULL,
  metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now(),
  completed_at timestamptz NULL,
  CONSTRAINT uq_leoft_sale_fulfillment_event UNIQUE (event_id),
  CONSTRAINT uq_leoft_sale_fulfillment_payment UNIQUE (company_id, payment_provider, payment_id),
  CONSTRAINT ck_leoft_sale_fulfillment_event_type CHECK (event_type = 'SALE_CONFIRMED'),
  CONSTRAINT ck_leoft_sale_fulfillment_payment_status CHECK (payment_status = 'confirmed'),
  CONSTRAINT ck_leoft_sale_fulfillment_status CHECK (status IN ('received','processing','completed','partial','failed','manual_review')),
  CONSTRAINT ck_leoft_sale_fulfillment_stage CHECK (current_stage IN ('received','sale_registered','contact_resolved','student_resolved','access_provisioned','program_assigned','crm_updated','post_sale_triggered','completed','manual_review')),
  CONSTRAINT ck_leoft_sale_fulfillment_post_sale CHECK (post_sale_status IN ('pending','queued','skipped','failed','sent','manual_review')),
  CONSTRAINT ck_leoft_sale_fulfillment_currency CHECK (currency IS NULL OR char_length(currency) = 3)
);

CREATE INDEX IF NOT EXISTS ix_leoft_sale_fulfillment_company_status
  ON leoft_sale_fulfillments(company_id, status, current_stage);
CREATE INDEX IF NOT EXISTS ix_leoft_sale_fulfillment_lead
  ON leoft_sale_fulfillments(company_id, lead_id, created_at);
CREATE INDEX IF NOT EXISTS ix_leoft_sale_fulfillment_student_program
  ON leoft_sale_fulfillments(company_id, student_id, student_program_id);

COMMIT;

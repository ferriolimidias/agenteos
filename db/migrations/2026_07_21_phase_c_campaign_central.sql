begin;

do $$
begin
  if to_regclass('public.meta_message_templates') is null then
    raise exception 'precheck failed: meta_message_templates missing (Phase B required)';
  end if;
  if to_regclass('public.campaign_recipients') is null then
    raise exception 'precheck failed: campaign_recipients missing (Phase A required)';
  end if;
  if to_regclass('public.followup_executions') is null then
    raise exception 'precheck failed: followup_executions missing (Phase A required)';
  end if;
end $$;

create table if not exists campaign_template_drafts (
  id uuid primary key,
  empresa_id uuid not null references empresas(id) on delete cascade,
  conexao_id uuid null,
  waba_id text null,
  internal_name text not null,
  technical_name text not null,
  language text not null,
  category text not null,
  description text null,
  status text not null default 'local_draft',
  components_json jsonb not null default '[]'::jsonb,
  variables_json jsonb not null default '[]'::jsonb,
  remote_template_id text null,
  source_template_id uuid null,
  remote_action_state text not null default 'local_only',
  rejection_reason text null,
  archived_at timestamptz null,
  submitted_at timestamptz null,
  created_by uuid null references usuarios(id) on delete set null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint fk_campaign_template_drafts_empresa_conexao foreign key (empresa_id, conexao_id) references conexoes(empresa_id, id),
  constraint ck_campaign_template_drafts_status check (status in ('local_draft','submitted','pending','approved','rejected','paused','disabled','archived','unknown')),
  constraint ck_campaign_template_drafts_technical_name check (length(technical_name) between 1 and 512 and technical_name ~ '^[a-z0-9_]+$'),
  constraint uq_campaign_template_drafts_name unique (empresa_id, conexao_id, waba_id, technical_name, language)
);

create table if not exists template_remote_action_audits (
  id uuid primary key,
  empresa_id uuid not null references empresas(id) on delete cascade,
  template_draft_id uuid null references campaign_template_drafts(id) on delete set null,
  meta_template_id uuid null references meta_message_templates(id) on delete set null,
  action text not null,
  status text not null default 'mocked',
  requested_by uuid null references usuarios(id) on delete set null,
  sanitized_payload_json jsonb not null default '{}'::jsonb,
  external_call_allowed boolean not null default false,
  idempotency_key text not null,
  created_at timestamptz not null default now(),
  unique (empresa_id, idempotency_key)
);

create table if not exists campaign_preparation_snapshots (
  id uuid primary key,
  empresa_id uuid not null references empresas(id) on delete cascade,
  campaign_id uuid not null,
  filters_json jsonb not null default '{}'::jsonb,
  inclusions_json jsonb not null default '[]'::jsonb,
  exclusions_json jsonb not null default '[]'::jsonb,
  audience_version integer not null default 1,
  idempotency_key text not null,
  created_at timestamptz not null default now(),
  unique (empresa_id, idempotency_key)
);

create table if not exists campaign_followup_rules (
  id uuid primary key,
  empresa_id uuid not null references empresas(id) on delete cascade,
  followup_id uuid null,
  name text not null,
  status text not null default 'draft',
  connection_id uuid null,
  channel text not null default 'whatsapp',
  trigger_json jsonb not null default '{}'::jsonb,
  steps_json jsonb not null default '[]'::jsonb,
  conditions_json jsonb not null default '{}'::jsonb,
  actions_json jsonb not null default '{}'::jsonb,
  timezone text not null default 'America/Sao_Paulo',
  allowed_windows_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  constraint fk_campaign_followup_rules_empresa_followup foreign key (empresa_id, followup_id) references config_followups(empresa_id, id),
  constraint fk_campaign_followup_rules_empresa_connection foreign key (empresa_id, connection_id) references conexoes(empresa_id, id)
);

create table if not exists whatsapp_message_policies (
  id uuid primary key,
  provider text not null,
  country text not null default '*',
  category text not null default '*',
  origin text not null default '*',
  version text not null,
  official_reference text not null,
  effective_from timestamptz not null,
  effective_to timestamptz null,
  rules_json jsonb not null default '{}'::jsonb,
  confirmed boolean not null default false,
  created_at timestamptz not null default now(),
  unique (provider, country, category, origin, version, effective_from)
);

create table if not exists whatsapp_pricing_policies (
  id uuid primary key,
  provider text not null,
  country text not null default '*',
  currency text not null,
  version text not null,
  official_reference text not null,
  effective_from timestamptz not null,
  effective_to timestamptz null,
  confirmed boolean not null default false,
  created_at timestamptz not null default now()
);

create table if not exists whatsapp_pricing_rates (
  id uuid primary key,
  policy_id uuid not null references whatsapp_pricing_policies(id) on delete cascade,
  provider text not null,
  country text not null,
  currency text not null,
  category text not null,
  value numeric(12,6) null,
  source text not null,
  effective_from timestamptz not null,
  effective_to timestamptz null,
  version text not null,
  created_at timestamptz not null default now(),
  unique (policy_id, country, category, effective_from)
);

create table if not exists message_cost_records (
  id uuid primary key,
  empresa_id uuid not null references empresas(id) on delete cascade,
  message_outbox_id uuid null,
  campaign_id uuid null,
  campaign_recipient_id uuid null,
  followup_execution_id uuid null,
  provider text not null,
  country text null,
  currency text null,
  category text null,
  charge_state text not null default 'unknown',
  pricing_source text null,
  tariffed boolean null,
  free boolean null,
  reason text null,
  estimated_value numeric(12,6) null,
  derived_value numeric(12,6) null,
  confirmed_value numeric(12,6) null,
  status text not null default 'unknown',
  created_at timestamptz not null default now(),
  constraint fk_message_cost_records_outbox foreign key (empresa_id, message_outbox_id) references message_outbox(empresa_id, id),
  constraint fk_message_cost_records_campaign_recipient foreign key (empresa_id, campaign_recipient_id) references campaign_recipients(empresa_id, id),
  constraint fk_message_cost_records_followup_execution foreign key (empresa_id, followup_execution_id) references followup_executions(empresa_id, id)
);

create table if not exists conversion_mappings (
  id uuid primary key,
  empresa_id uuid not null references empresas(id) on delete cascade,
  source_type text not null,
  source_id text not null,
  internal_event text not null,
  external_event text not null,
  default_value numeric(12,2) null,
  currency text null,
  requires_authorization boolean not null default true,
  deduplication_policy text not null,
  active boolean not null default true,
  created_at timestamptz not null default now(),
  unique (empresa_id, source_type, source_id, external_event)
);

create table if not exists business_conversion_events (
  id uuid primary key,
  empresa_id uuid not null references empresas(id) on delete cascade,
  mapping_id uuid null references conversion_mappings(id) on delete set null,
  lead_id uuid null,
  contact_id uuid null,
  conversation_id uuid null,
  campaign_id uuid null,
  ad_campaign_id text null,
  ad_set_id text null,
  ad_id text null,
  click_id text null,
  dataset_id text null,
  event_id text not null,
  internal_event text not null,
  external_event text null,
  value numeric(12,2) null,
  currency text null,
  status text not null default 'pending',
  retry_count integer not null default 0,
  sanitized_error text null,
  idempotency_key text not null,
  created_at timestamptz not null default now(),
  sent_at timestamptz null,
  accepted_at timestamptz null,
  failed_at timestamptz null,
  constraint fk_business_conversion_events_lead foreign key (empresa_id, lead_id) references crm_leads(empresa_id, id),
  constraint fk_business_conversion_events_contact foreign key (empresa_id, contact_id) references channel_contacts(empresa_id, id),
  constraint fk_business_conversion_events_conversation foreign key (empresa_id, conversation_id) references channel_conversations(empresa_id, id),
  unique (empresa_id, idempotency_key),
  unique (empresa_id, event_id)
);

create table if not exists whatsapp_ad_attributions (
  id uuid primary key,
  empresa_id uuid not null references empresas(id) on delete cascade,
  lead_id uuid null,
  conversation_id uuid null,
  conexao_id uuid null,
  waba_id text null,
  origin text not null default 'click_to_whatsapp',
  click_id text null,
  ad_campaign_id text null,
  ad_set_id text null,
  ad_id text null,
  clicked_at timestamptz null,
  raw_referral_json jsonb not null default '{}'::jsonb,
  created_at timestamptz not null default now(),
  constraint fk_whatsapp_ad_attributions_lead foreign key (empresa_id, lead_id) references crm_leads(empresa_id, id),
  constraint fk_whatsapp_ad_attributions_conversation foreign key (empresa_id, conversation_id) references channel_conversations(empresa_id, id),
  constraint fk_whatsapp_ad_attributions_conexao foreign key (empresa_id, conexao_id) references conexoes(empresa_id, id)
);

create index if not exists ix_campaign_template_drafts_lookup on campaign_template_drafts (empresa_id, conexao_id, status, category);
create index if not exists ix_campaign_preparation_snapshots_campaign on campaign_preparation_snapshots (empresa_id, campaign_id, created_at);
create index if not exists ix_campaign_followup_rules_lookup on campaign_followup_rules (empresa_id, status, channel);
create index if not exists ix_whatsapp_message_policies_effective on whatsapp_message_policies (provider, country, category, origin, effective_from, effective_to);
create index if not exists ix_whatsapp_pricing_rates_lookup on whatsapp_pricing_rates (provider, country, category, effective_from, effective_to);
create index if not exists ix_message_cost_records_lookup on message_cost_records (empresa_id, provider, status, category, created_at);
create index if not exists ix_business_conversion_events_lookup on business_conversion_events (empresa_id, status, external_event, created_at);
create index if not exists ix_whatsapp_ad_attributions_lookup on whatsapp_ad_attributions (empresa_id, lead_id, conversation_id, click_id);

do $$
begin
  if to_regclass('public.campaign_template_drafts') is null then
    raise exception 'postcheck failed: campaign_template_drafts missing';
  end if;
  if to_regclass('public.whatsapp_message_policies') is null then
    raise exception 'postcheck failed: whatsapp_message_policies missing';
  end if;
  if to_regclass('public.business_conversion_events') is null then
    raise exception 'postcheck failed: business_conversion_events missing';
  end if;
end $$;

commit;

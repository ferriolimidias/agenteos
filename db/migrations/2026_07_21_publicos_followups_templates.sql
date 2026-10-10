begin;

do $$
begin
  if to_regclass('public.crm_leads') is null then
    raise exception 'precheck failed: crm_leads missing';
  end if;
  if to_regclass('public.tags_crm') is null then
    raise exception 'precheck failed: tags_crm missing';
  end if;
  if to_regclass('public.config_followups') is null then
    raise exception 'precheck failed: config_followups missing';
  end if;
  if to_regclass('public.followup_executions') is null then
    raise exception 'precheck failed: followup_executions missing';
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'uq_tags_crm_empresa_id_id') then
    alter table tags_crm add constraint uq_tags_crm_empresa_id_id unique (empresa_id, id);
  end if;
end $$;

create table if not exists crm_tag_events (
  id uuid primary key,
  empresa_id uuid not null references empresas(id) on delete cascade,
  lead_id uuid not null references crm_leads(id) on delete cascade,
  contact_id uuid null references channel_contacts(id) on delete set null,
  conversation_id uuid null references channel_conversations(id) on delete set null,
  tag_id uuid not null references tags_crm(id) on delete cascade,
  action varchar not null,
  source varchar not null default 'unknown',
  source_reference_id varchar null,
  actor_user_id uuid null references usuarios(id) on delete set null,
  occurred_at timestamp not null default now(),
  idempotency_key varchar not null,
  metadata_json jsonb not null default '{}'::jsonb,
  created_at timestamp default now(),
  constraint fk_crm_tag_events_empresa_lead foreign key (empresa_id, lead_id) references crm_leads(empresa_id, id),
  constraint fk_crm_tag_events_empresa_tag foreign key (empresa_id, tag_id) references tags_crm(empresa_id, id),
  constraint fk_crm_tag_events_empresa_contact foreign key (empresa_id, contact_id) references channel_contacts(empresa_id, id),
  constraint fk_crm_tag_events_empresa_conversation foreign key (empresa_id, conversation_id) references channel_conversations(empresa_id, id),
  constraint uq_crm_tag_events_empresa_id_id unique (empresa_id, id),
  constraint uq_crm_tag_events_idempotency unique (empresa_id, idempotency_key),
  constraint ck_crm_tag_events_action check (action in ('applied','removed')),
  constraint ck_crm_tag_events_source check (source in ('manual','ai','funnel','automation','followup','campaign','csv_import','integration','system','unknown'))
);

create table if not exists contact_import_batches (
  id uuid primary key,
  empresa_id uuid not null references empresas(id) on delete cascade,
  filename varchar not null,
  source varchar not null default 'importacao_manual',
  tag_id uuid not null references tags_crm(id) on delete restrict,
  status varchar not null default 'completed',
  total_rows integer not null default 0,
  inserted_count integer not null default 0,
  updated_count integer not null default 0,
  duplicate_count integer not null default 0,
  invalid_count integer not null default 0,
  conflict_count integer not null default 0,
  blocked_count integer not null default 0,
  opted_out_count integer not null default 0,
  unknown_consent_count integer not null default 0,
  opted_in_count integer not null default 0,
  started_at timestamp null,
  completed_at timestamp null,
  initiated_by uuid null references usuarios(id) on delete set null,
  idempotency_key varchar not null,
  mapping_json jsonb not null default '{}'::jsonb,
  error_summary jsonb not null default '{}'::jsonb,
  created_at timestamp default now(),
  updated_at timestamp default now(),
  constraint fk_contact_import_batches_empresa_tag foreign key (empresa_id, tag_id) references tags_crm(empresa_id, id),
  constraint uq_contact_import_batches_empresa_id_id unique (empresa_id, id),
  constraint uq_contact_import_batches_idempotency unique (empresa_id, idempotency_key)
);

create table if not exists contact_import_errors (
  id uuid primary key,
  empresa_id uuid not null references empresas(id) on delete cascade,
  batch_id uuid not null references contact_import_batches(id) on delete cascade,
  row_number integer not null,
  reason varchar not null,
  sanitized_row_json jsonb not null default '{}'::jsonb,
  created_at timestamp default now(),
  constraint fk_contact_import_errors_empresa_batch foreign key (empresa_id, batch_id) references contact_import_batches(empresa_id, id)
);

create table if not exists saved_audiences (
  id uuid primary key,
  empresa_id uuid not null references empresas(id) on delete cascade,
  name varchar not null,
  description text null,
  status varchar not null default 'active',
  rules_json jsonb not null default '{}'::jsonb,
  preview_json jsonb not null default '{}'::jsonb,
  idempotency_key varchar not null,
  created_at timestamp default now(),
  updated_at timestamp default now(),
  constraint uq_saved_audiences_empresa_name unique (empresa_id, name),
  constraint uq_saved_audiences_empresa_id_id unique (empresa_id, id),
  constraint ck_saved_audiences_status check (status in ('active','archived'))
);

create table if not exists followup_sequence_steps (
  id uuid primary key,
  empresa_id uuid not null references empresas(id) on delete cascade,
  followup_id uuid not null references config_followups(id) on delete cascade,
  step_order integer not null default 1,
  status varchar not null default 'active',
  trigger_tag_id uuid null references tags_crm(id) on delete restrict,
  delay_minutes integer not null default 0,
  timezone varchar not null default 'America/Sao_Paulo',
  ad_window_mode varchar not null default 'template_after_expiry',
  message_free_text text null,
  template_id uuid null references meta_message_templates(id) on delete set null,
  variable_mapping_json jsonb not null default '[]'::jsonb,
  execute_conditions_json jsonb not null default '{}'::jsonb,
  cancel_conditions_json jsonb not null default '{}'::jsonb,
  post_send_tag_id uuid null references tags_crm(id) on delete set null,
  actions_json jsonb not null default '[]'::jsonb,
  created_at timestamp default now(),
  updated_at timestamp default now(),
  constraint fk_followup_sequence_steps_empresa_followup foreign key (empresa_id, followup_id) references config_followups(empresa_id, id),
  constraint fk_followup_sequence_steps_empresa_trigger_tag foreign key (empresa_id, trigger_tag_id) references tags_crm(empresa_id, id),
  constraint fk_followup_sequence_steps_empresa_template foreign key (empresa_id, template_id) references meta_message_templates(empresa_id, id),
  constraint fk_followup_sequence_steps_empresa_post_tag foreign key (empresa_id, post_send_tag_id) references tags_crm(empresa_id, id),
  constraint uq_followup_sequence_steps_order unique (empresa_id, followup_id, step_order),
  constraint uq_followup_sequence_steps_empresa_id_id unique (empresa_id, id),
  constraint ck_followup_sequence_steps_status check (status in ('active','paused','archived')),
  constraint ck_followup_sequence_steps_ad_window_mode check (ad_window_mode in ('cancel_on_expiry','template_after_expiry','require_template_when_unknown','block_when_unknown'))
);

alter table followup_executions add column if not exists trigger_event_id uuid null references crm_tag_events(id) on delete set null;
alter table followup_executions add column if not exists triggered_at timestamp null;
alter table followup_executions add column if not exists trigger_tag_id uuid null references tags_crm(id) on delete set null;
alter table followup_executions add column if not exists policy_version varchar null;
alter table followup_executions add column if not exists policy_snapshot_json jsonb not null default '{}'::jsonb;
alter table followup_executions add column if not exists ad_entry_started_at timestamp null;
alter table followup_executions add column if not exists ad_entry_expires_at timestamp null;
alter table followup_executions add column if not exists ad_entry_window_state varchar null;

create unique index if not exists uq_followup_execution_trigger_event_step
  on followup_executions (empresa_id, followup_id, lead_id, trigger, trigger_event_id)
  where trigger_event_id is not null;

create index if not exists ix_crm_tag_events_lookup on crm_tag_events (empresa_id, tag_id, lead_id, action, occurred_at);
create index if not exists ix_contact_import_batches_lookup on contact_import_batches (empresa_id, tag_id, status, created_at);
create index if not exists ix_contact_import_errors_batch on contact_import_errors (empresa_id, batch_id, row_number);
create index if not exists ix_saved_audiences_lookup on saved_audiences (empresa_id, status, created_at);
create index if not exists ix_followup_sequence_steps_lookup on followup_sequence_steps (empresa_id, followup_id, trigger_tag_id, status);

do $$
begin
  if to_regclass('public.crm_tag_events') is null then
    raise exception 'postcheck failed: crm_tag_events missing';
  end if;
  if to_regclass('public.contact_import_batches') is null then
    raise exception 'postcheck failed: contact_import_batches missing';
  end if;
  if to_regclass('public.saved_audiences') is null then
    raise exception 'postcheck failed: saved_audiences missing';
  end if;
  if to_regclass('public.followup_sequence_steps') is null then
    raise exception 'postcheck failed: followup_sequence_steps missing';
  end if;
end $$;

commit;

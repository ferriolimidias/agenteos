begin;

do $$
begin
  if to_regclass('public.empresas') is null then
    raise exception 'precheck failed: empresas missing';
  end if;
  if to_regclass('public.crm_leads') is null then
    raise exception 'precheck failed: crm_leads missing';
  end if;
end $$;

create table if not exists leoft_training_plans (
  id uuid primary key,
  empresa_id uuid not null references empresas(id) on delete cascade,
  lead_id uuid not null references crm_leads(id) on delete cascade,
  conversation_id text null,
  token_reference text null,
  anamnese_token_hash text null,
  anamnese_status text null,
  payment_reference text null,
  payment_status text null,
  payment_mode text null,
  payment_provider text null,
  product_name text not null default 'Método Híbrido LeoFT',
  status text not null default 'pending_approval',
  student_name text null,
  student_email text null,
  student_whatsapp text null,
  plan_json jsonb not null default '{}'::jsonb,
  hermes_payload jsonb null,
  hermes_mode text not null default 'mock',
  telegram_status text not null default 'dry_run',
  telegram_payload jsonb null,
  telegram_sent_at timestamp null,
  approved_at timestamp null,
  approved_by text null,
  approval_notes text null,
  rejected_at timestamp null,
  rejected_by text null,
  rejection_reason text null,
  revision_requested_at timestamp null,
  revision_requested_by text null,
  revision_notes text null,
  created_at timestamp not null default now(),
  updated_at timestamp not null default now()
);

alter table leoft_training_plans add column if not exists conversation_id text null;
alter table leoft_training_plans add column if not exists token_reference text null;
alter table leoft_training_plans add column if not exists anamnese_token_hash text null;
alter table leoft_training_plans add column if not exists anamnese_status text null;
alter table leoft_training_plans add column if not exists payment_reference text null;
alter table leoft_training_plans add column if not exists payment_status text null;
alter table leoft_training_plans add column if not exists payment_mode text null;
alter table leoft_training_plans add column if not exists payment_provider text null;
alter table leoft_training_plans add column if not exists product_name text not null default 'Método Híbrido LeoFT';
alter table leoft_training_plans add column if not exists status text not null default 'pending_approval';
alter table leoft_training_plans add column if not exists student_name text null;
alter table leoft_training_plans add column if not exists student_email text null;
alter table leoft_training_plans add column if not exists student_whatsapp text null;
alter table leoft_training_plans add column if not exists plan_json jsonb not null default '{}'::jsonb;
alter table leoft_training_plans add column if not exists hermes_payload jsonb null;
alter table leoft_training_plans add column if not exists hermes_mode text not null default 'mock';
alter table leoft_training_plans add column if not exists telegram_status text not null default 'dry_run';
alter table leoft_training_plans add column if not exists telegram_payload jsonb null;
alter table leoft_training_plans add column if not exists telegram_sent_at timestamp null;
alter table leoft_training_plans add column if not exists approved_at timestamp null;
alter table leoft_training_plans add column if not exists approved_by text null;
alter table leoft_training_plans add column if not exists approval_notes text null;
alter table leoft_training_plans add column if not exists rejected_at timestamp null;
alter table leoft_training_plans add column if not exists rejected_by text null;
alter table leoft_training_plans add column if not exists rejection_reason text null;
alter table leoft_training_plans add column if not exists revision_requested_at timestamp null;
alter table leoft_training_plans add column if not exists revision_requested_by text null;
alter table leoft_training_plans add column if not exists revision_notes text null;
alter table leoft_training_plans add column if not exists created_at timestamp not null default now();
alter table leoft_training_plans add column if not exists updated_at timestamp not null default now();

alter table leoft_training_plans alter column product_name set default 'Método Híbrido LeoFT';
alter table leoft_training_plans alter column status set default 'pending_approval';
alter table leoft_training_plans alter column hermes_mode set default 'mock';
alter table leoft_training_plans alter column telegram_status set default 'dry_run';
alter table leoft_training_plans alter column plan_json set default '{}'::jsonb;
alter table leoft_training_plans alter column created_at set default now();
alter table leoft_training_plans alter column updated_at set default now();

update leoft_training_plans
set token_reference = anamnese_token_hash
where token_reference is null
  and anamnese_token_hash is not null;

create table if not exists leoft_student_accesses (
  id uuid primary key,
  empresa_id uuid not null references empresas(id) on delete cascade,
  lead_id uuid not null references crm_leads(id) on delete cascade,
  plan_id uuid not null references leoft_training_plans(id) on delete cascade,
  student_email text not null,
  student_whatsapp text null,
  login_identifier text not null,
  password_hash text not null,
  temporary_password_masked text null,
  access_token_hash text not null,
  access_token_masked text not null,
  app_url text null,
  status text not null default 'active',
  created_at timestamp not null default now(),
  updated_at timestamp not null default now(),
  first_login_at timestamp null,
  revoked_at timestamp null
);

alter table leoft_student_accesses add column if not exists student_email text;
alter table leoft_student_accesses add column if not exists student_whatsapp text null;
alter table leoft_student_accesses add column if not exists login_identifier text;
alter table leoft_student_accesses add column if not exists password_hash text;
alter table leoft_student_accesses add column if not exists temporary_password_masked text null;
alter table leoft_student_accesses add column if not exists access_token_hash text;
alter table leoft_student_accesses add column if not exists access_token_masked text;
alter table leoft_student_accesses add column if not exists app_url text null;
alter table leoft_student_accesses add column if not exists status text not null default 'active';
alter table leoft_student_accesses add column if not exists created_at timestamp not null default now();
alter table leoft_student_accesses add column if not exists updated_at timestamp not null default now();
alter table leoft_student_accesses add column if not exists first_login_at timestamp null;
alter table leoft_student_accesses add column if not exists revoked_at timestamp null;

alter table leoft_student_accesses alter column status set default 'active';
alter table leoft_student_accesses alter column created_at set default now();
alter table leoft_student_accesses alter column updated_at set default now();

create index if not exists ix_leoft_training_plans_empresa_id on leoft_training_plans (empresa_id);
create index if not exists ix_leoft_training_plans_lead_id on leoft_training_plans (lead_id);
create index if not exists ix_leoft_training_plans_status on leoft_training_plans (status);
create index if not exists ix_leoft_training_plans_payment_reference on leoft_training_plans (payment_reference);
create index if not exists ix_leoft_training_plans_created_at on leoft_training_plans (created_at);

create index if not exists ix_leoft_student_accesses_empresa_id on leoft_student_accesses (empresa_id);
create index if not exists ix_leoft_student_accesses_lead_id on leoft_student_accesses (lead_id);
create index if not exists ix_leoft_student_accesses_plan_id on leoft_student_accesses (plan_id);
create index if not exists ix_leoft_student_accesses_login_identifier on leoft_student_accesses (login_identifier);
create index if not exists ix_leoft_student_accesses_access_token_hash on leoft_student_accesses (access_token_hash);

do $$
begin
  if not exists (
    select 1 from (
      select empresa_id, payment_reference
      from leoft_training_plans
      where payment_reference is not null
      group by empresa_id, payment_reference
      having count(*) > 1
    ) duplicates
  ) then
    create unique index if not exists ux_leoft_training_plans_empresa_payment_reference
      on leoft_training_plans (empresa_id, payment_reference)
      where payment_reference is not null;
  else
    raise notice 'skipping ux_leoft_training_plans_empresa_payment_reference: duplicates exist';
  end if;
end $$;

do $$
begin
  if not exists (
    select 1 from (
      select plan_id
      from leoft_student_accesses
      where status = 'active'
      group by plan_id
      having count(*) > 1
    ) duplicates
  ) then
    create unique index if not exists ux_leoft_student_accesses_active_plan_id
      on leoft_student_accesses (plan_id)
      where status = 'active';
  else
    raise notice 'skipping ux_leoft_student_accesses_active_plan_id: duplicate active accesses exist';
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from leoft_training_plans
    where status is null
       or status not in ('pending_approval', 'approved', 'rejected', 'revision_requested')
  ) and not exists (
    select 1 from pg_constraint where conname = 'ck_leoft_training_plans_status'
  ) then
    alter table leoft_training_plans
      add constraint ck_leoft_training_plans_status
      check (status in ('pending_approval', 'approved', 'rejected', 'revision_requested'));
  else
    raise notice 'skipping ck_leoft_training_plans_status if already present or incompatible';
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from leoft_training_plans
    where hermes_mode is not null
      and hermes_mode not in ('mock', 'real', 'dry_run')
  ) and not exists (
    select 1 from pg_constraint where conname = 'ck_leoft_training_plans_hermes_mode'
  ) then
    alter table leoft_training_plans
      add constraint ck_leoft_training_plans_hermes_mode
      check (hermes_mode is null or hermes_mode in ('mock', 'real', 'dry_run'));
  else
    raise notice 'skipping ck_leoft_training_plans_hermes_mode if already present or incompatible';
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from leoft_training_plans
    where telegram_status is not null
      and telegram_status not in ('dry_run', 'sent', 'failed', 'disabled')
  ) and not exists (
    select 1 from pg_constraint where conname = 'ck_leoft_training_plans_telegram_status'
  ) then
    alter table leoft_training_plans
      add constraint ck_leoft_training_plans_telegram_status
      check (telegram_status is null or telegram_status in ('dry_run', 'sent', 'failed', 'disabled'));
  else
    raise notice 'skipping ck_leoft_training_plans_telegram_status if already present or incompatible';
  end if;
end $$;

do $$
begin
  if not exists (
    select 1
    from leoft_student_accesses
    where status is null
       or status not in ('active', 'revoked', 'expired')
  ) and not exists (
    select 1 from pg_constraint where conname = 'ck_leoft_student_accesses_status'
  ) then
    alter table leoft_student_accesses
      add constraint ck_leoft_student_accesses_status
      check (status in ('active', 'revoked', 'expired'));
  else
    raise notice 'skipping ck_leoft_student_accesses_status if already present or incompatible';
  end if;
end $$;

do $$
begin
  if to_regclass('public.leoft_training_plans') is null then
    raise exception 'postcheck failed: leoft_training_plans missing';
  end if;
  if to_regclass('public.leoft_student_accesses') is null then
    raise exception 'postcheck failed: leoft_student_accesses missing';
  end if;
end $$;

commit;

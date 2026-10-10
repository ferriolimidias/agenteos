begin;

create table if not exists leoft_students (
  id uuid primary key default gen_random_uuid(),
  empresa_id uuid not null references empresas(id) on delete cascade,
  name text not null,
  email text null,
  whatsapp text null,
  active boolean not null default true,
  crm_lead_id uuid null references crm_leads(id) on delete set null,
  assessment_id text null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table leoft_student_accesses add column if not exists student_id uuid null;
alter table leoft_student_accesses alter column lead_id drop not null;
alter table leoft_student_accesses alter column plan_id drop not null;
alter table leoft_student_accesses alter column student_email drop not null;

do $$
begin
  if not exists (select 1 from pg_constraint where conname = 'leoft_student_accesses_student_id_fkey') then
    alter table leoft_student_accesses add constraint leoft_student_accesses_student_id_fkey
      foreign key (student_id) references leoft_students(id) on delete cascade;
  end if;
end $$;

create index if not exists ix_leoft_students_empresa_id on leoft_students (empresa_id);
create index if not exists ix_leoft_students_name on leoft_students (empresa_id, name);
create index if not exists ix_leoft_students_email on leoft_students (empresa_id, email);
create index if not exists ix_leoft_students_crm_lead_id on leoft_students (crm_lead_id);
create index if not exists ix_leoft_student_accesses_student_id on leoft_student_accesses (student_id);
create unique index if not exists ux_leoft_student_accesses_active_student
  on leoft_student_accesses (student_id)
  where status = 'active' and student_id is not null;

commit;

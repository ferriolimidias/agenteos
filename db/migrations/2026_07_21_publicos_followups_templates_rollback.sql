begin;

drop index if exists uq_followup_execution_trigger_event_step;

alter table followup_executions drop column if exists ad_entry_window_state;
alter table followup_executions drop column if exists ad_entry_expires_at;
alter table followup_executions drop column if exists ad_entry_started_at;
alter table followup_executions drop column if exists policy_snapshot_json;
alter table followup_executions drop column if exists policy_version;
alter table followup_executions drop column if exists trigger_tag_id;
alter table followup_executions drop column if exists triggered_at;
alter table followup_executions drop column if exists trigger_event_id;

drop table if exists followup_sequence_steps;
drop table if exists saved_audiences;
drop table if exists contact_import_errors;
drop table if exists contact_import_batches;
drop table if exists crm_tag_events;

alter table tags_crm drop constraint if exists uq_tags_crm_empresa_id_id;

commit;

begin;

alter table campaign_template_drafts
  drop constraint if exists ck_campaign_template_drafts_technical_name;

alter table campaign_template_drafts
  add constraint ck_campaign_template_drafts_technical_name
  check (
    length(technical_name) between 1 and 512
    and technical_name ~ '^[a-z0-9_]+$'
  );

commit;

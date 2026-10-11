"""Read-only, evidence-based commercial cohorts; no reconciliation or transports.

All lead metrics and drill predicates use the same set-based PostgreSQL projection.
Only persisted configuration is read: this service never compiles/synchronizes a funnel.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import uuid
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from sqlalchemy import select, text
from db.models import Empresa, TagCRM
from app.services.monitor_funnel import parse_managed_instruction
from app.services.commercial_journey import _label

STATUSES = ('active', 'inactive', 'converted', 'lost', 'indeterminate')
ORIGINS = ('whatsapp', 'instagram', 'web', 'sms', 'telegram', 'unknown', 'ctwa_referral', 'tracked_link_click', 'manual_touch', 'unknown_source')
LIMITATIONS = [
    'Uma unidade é um contato CRM distinto; cliques Meta não são leads.',
    'Histórico de tags é parcial; snapshots CRM não reconstroem transições.',
    'Inatividade e ausência de avanço são observações, não motivos de abandono.',
    'Vínculo Ads em qualquer aquisição persistida não prova a causa da primeira entrada; filtros Ads não são aditivos.',
    'Retorno após follow-up é correlação temporal na mesma conversa, não causalidade.',
    'Sem pagamento confirmado, receita é desconhecida; tag de compra e CRM fechado não comprovam venda.',
]


def enabled():
    return os.getenv('COMMERCIAL_FUNNEL_DASHBOARD_ENABLED', 'true').strip().lower() not in {'false', '0', 'off'}


def _datetime(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, datetime.min.time(), timezone.utc)
    if not isinstance(value, datetime):
        raise ValueError('invalid time')
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def filters(*, empresa_id, start=None, end=None, observed_at=None, inactivity_hours=168,
            return_window_days=7, stage=None, origin=None, campaign_id=None, ad_id=None,
            status=None, bottleneck=None, limit=25, offset=0, now=None):
    now = _datetime(now or datetime.now(timezone.utc))
    observed = _datetime(observed_at) if observed_at else now
    end = _datetime(end) if end else observed
    start = _datetime(start) if start else end - timedelta(days=90)
    latest_end = now.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    if observed > now or start > observed or start >= end or end > latest_end or (end-start) > timedelta(days=366):
        raise ValueError('invalid observation window')
    for value, low, high in ((inactivity_hours,1,8760),(return_window_days,1,90),(limit,1,100),(offset,0,100000)):
        if isinstance(value,bool) or not isinstance(value,int) or not low <= value <= high:
            raise ValueError('invalid numeric bound')
    if origin is not None and origin not in ORIGINS or status is not None and status not in STATUSES:
        raise ValueError('invalid enum')
    for value in (campaign_id, ad_id):
        if value is not None and not re.fullmatch(r'[0-9]{1,40}', value):
            raise ValueError('invalid Ads identity')
    if stage is not None and (not isinstance(stage,str) or not re.fullmatch(r'[a-zA-Z0-9_:-]{1,100}',stage)):
        raise ValueError('invalid stage')
    if bottleneck is not None and (not isinstance(bottleneck,str) or not re.fullmatch(r'[a-zA-Z0-9_:-]{1,110}',bottleneck)):
        raise ValueError('invalid bottleneck')
    return dict(tenant=str(uuid.UUID(str(empresa_id))), start=start, end=end, observed=observed,
                inactivity_hours=inactivity_hours, return_window_days=return_window_days,
                stage=stage, origin=origin, campaign_id=campaign_id, ad_id=ad_id,
                status=status, bottleneck=bottleneck, limit=limit, offset=offset)


def _window(p):
    return {'start':p['start'].isoformat(), 'end':p['end'].isoformat(), 'observed_at':p['observed'].isoformat(),
            'inactivity_hours':p['inactivity_hours'], 'return_window_days':p['return_window_days'],
            'entry_definition':'Primeira conversa ou inbound elegível em toda a história até o horizonte; intervalo [start,end) UTC.',
            'unit':'distinct_crm_lead'}


def _rate(numerator, denominator):
    return {'numerator':numerator,'denominator':denominator,
            'value':numerator/denominator if numerator is not None and denominator else None,
            'state':'observed' if numerator is not None and denominator else 'unknown'}


async def _configuration(session, p):
    row = (await session.execute(select(Empresa.id, Empresa.credenciais_canais['monitor_funnel_spec']).where(Empresa.id == uuid.UUID(p['tenant'])))).first()
    if row is None:
        raise LookupError('tenant unavailable')
    spec = row[1] if isinstance(row[1],dict) else {}
    stages, seen = [], set()
    for index,item in enumerate((spec.get('stages') if isinstance(spec.get('stages'),list) else [])[:40]):
        if not isinstance(item,dict):
            continue
        key = item.get('key')
        if not isinstance(key,str) or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}',key) or key in seen:
            continue
        seen.add(key)
        order = item.get('order')
        stages.append({'key':key,'label':_label(item.get('display_name')) or key,'order':order if isinstance(order,int) and not isinstance(order,bool) else index,
                       'source':'persisted_funnel_spec','meta_event':item.get('meta_event') if item.get('meta_event') in ('ViewContent','LeadSubmitted','QualifiedLead','AddToCart','InitiateCheckout','OrderCreated','CartAbandoned','Purchase','Refund') else None})
    has_spec = bool(stages)
    tags = (await session.execute(select(TagCRM.id,TagCRM.nome,TagCRM.instrucao_ia,TagCRM.ativa_no_funil,TagCRM.ordem).where(TagCRM.empresa_id == uuid.UUID(p['tenant'])))).all()
    mappings = []
    for tag in tags:
        metadata = parse_managed_instruction(tag[2])
        metadata = metadata if isinstance(metadata,dict) else {}
        key = metadata.get('funnel_stage_key')
        managed = metadata.get('managed_by_monitor_funnel') is True and isinstance(metadata.get('funnel_version'),str) and bool(metadata['funnel_version']) and metadata.get('active') is not False
        if has_spec:
            if not managed or key not in seen:
                continue
        elif tag[3]:
            key = 'tag:'+str(tag[0])
            if key not in seen:
                seen.add(key)
                stages.append({'key':key,'label':_label(tag[1]) or 'Etapa legada','order':tag[4] if isinstance(tag[4],int) else len(stages),
                               'source':'legacy_funnel_tag','meta_event':metadata.get('meta_event_name')})
        else:
            continue
        mappings.append({'tag_id':str(tag[0]),'key':key})
    stages.sort(key=lambda item:(item['order'],item['key']))
    if p['stage'] is not None and p['stage'] not in seen:
        raise LookupError('stage unavailable')
    if p['bottleneck'] is not None and p['bottleneck'][len('inactive:'):] not in seen:
        raise LookupError('bottleneck unavailable')
    p['stages_json'] = json.dumps(stages)
    p['tags_json'] = json.dumps(mappings)
    return stages


# SQL parameters are bound, including JSON configuration and every filter. Strong
# parent joins exclude legacy cross-tenant references even in older schemas.
# Ledger refunds are signed debits; normalize only verified refunds to a magnitude
# before comparing their identity with positive Commercial Event projections.
_CTE = r"""
WITH tenant_leads AS MATERIALIZED (
 SELECT l.* FROM crm_leads l WHERE l.empresa_id=CAST(:tenant AS uuid)
), conversations AS MATERIALIZED (
 SELECT c.* FROM channel_conversations c JOIN tenant_leads l ON l.id=c.lead_id AND l.empresa_id=c.empresa_id
 WHERE c.criado_em <= (CAST(:observed AS timestamptz) AT TIME ZONE 'UTC')
), messages AS MATERIALIZED (
 SELECT m.*,CASE WHEN m.direction IN ('inbound','outbound') THEN m.direction
 WHEN m.from_me IS TRUE THEN 'outbound' WHEN m.from_me IS FALSE THEN 'inbound' ELSE 'unknown' END AS normalized_direction
 FROM mensagens_historico m JOIN tenant_leads l ON l.id=m.lead_id AND l.empresa_id=m.empresa_id
 WHERE m.criado_em <= (CAST(:observed AS timestamptz) AT TIME ZONE 'UTC')
 AND lower(COALESCE(m.tipo_mensagem,'')) NOT IN ('status','reaction')
 AND lower(COALESCE(m.metadata_json->>'event_kind','')) NOT IN ('status','reaction')
), entries AS (
 SELECT lead_id,min(at) AS entry_at FROM (
 SELECT lead_id,criado_em AT TIME ZONE 'UTC' AS at FROM conversations
 UNION ALL SELECT lead_id,criado_em AT TIME ZONE 'UTC' FROM messages WHERE normalized_direction='inbound'
 ) e GROUP BY lead_id
), cohort AS MATERIALIZED (
 SELECT l.*,e.entry_at FROM tenant_leads l JOIN entries e ON e.lead_id=l.id
 WHERE e.entry_at>=CAST(:start AS timestamptz) AND e.entry_at<CAST(:end AS timestamptz)
), configured AS (
 SELECT * FROM jsonb_to_recordset(CAST(:stages_json AS jsonb)) AS s(key text,label text,"order" integer,source text,meta_event text)
), tag_mapping AS (
 SELECT * FROM jsonb_to_recordset(CAST(:tags_json AS jsonb)) AS m(tag_id uuid,key text)
), acquisitions AS MATERIALIZED (
 SELECT t.lead_id,t.occurred_at,t.channel,t.event_kind,r.resolution_status,
 CASE WHEN r.resolution_status='resolved' AND r.ad_id~'^[0-9]{1,40}$' AND r.adset_id~'^[0-9]{1,40}$' AND r.campaign_id~'^[0-9]{1,40}$' THEN true ELSE false END AS exact,
 CASE WHEN r.resolution_status='resolved' AND r.ad_id~'^[0-9]{1,40}$' AND r.adset_id~'^[0-9]{1,40}$' AND r.campaign_id~'^[0-9]{1,40}$' THEN r.campaign_id END AS campaign_id,
 CASE WHEN r.resolution_status='resolved' AND r.ad_id~'^[0-9]{1,40}$' AND r.adset_id~'^[0-9]{1,40}$' AND r.campaign_id~'^[0-9]{1,40}$' THEN r.ad_id END AS ad_id
 FROM attribution_touches t JOIN cohort l ON t.lead_id=l.id AND t.empresa_id=l.empresa_id
 LEFT JOIN attribution_touch_resolutions r ON r.touch_id=t.id AND r.empresa_id=t.empresa_id
 WHERE t.event_kind IN ('ctwa_referral','tracked_link_click','manual_touch','unknown_source') AND t.occurred_at<=CAST(:observed AS timestamptz)
), financial_raw AS (
 SELECT t.lead_id,t.occurred_at,t.provider,t.payment_id,
 CASE WHEN t.event_kind='refund' THEN abs(t.amount) ELSE t.amount END AS amount,t.currency,
 CASE WHEN t.event_kind='refund' THEN 'refund' ELSE 'purchase' END AS kind, 1 AS priority
 FROM attribution_touches t JOIN tenant_leads l ON t.lead_id=l.id AND t.empresa_id=l.empresa_id
 WHERE t.occurred_at<=CAST(:observed AS timestamptz) AND t.event_kind IN ('payment_approved','refund')
 AND t.touch_source='payment_webhook' AND t.metadata_json->>'provenance'='provider_verified_payment_status'
 AND COALESCE(t.metadata_json->>'environment','production')='production'
 AND lower(COALESCE(t.metadata_json->>'simulated','false')) NOT IN ('true','1','yes','on')
 AND lower(COALESCE(t.metadata_json->>'is_simulated','false')) NOT IN ('true','1','yes','on')
 AND t.provider IN ('mercado_pago','stripe','paypal','asaas','pagarme','pagseguro','efi','adyen')
 AND length(trim(t.payment_id))>0 AND (t.event_kind='refund' OR t.amount>=0)
 AND abs(t.amount)<'1000000000000000'::numeric AND t.currency~'^[A-Z]{3}$'
 UNION ALL
 SELECT e.lead_id,e.occurred_at,e.provider,e.payment_id,e.value,e.currency,
 CASE WHEN e.event_name='Refund' THEN 'refund' ELSE 'purchase' END,2
 FROM commercial_events e JOIN tenant_leads l ON l.id=e.lead_id AND l.empresa_id=e.empresa_id
 WHERE e.occurred_at<=CAST(:observed AS timestamptz) AND e.event_name IN ('Purchase','Refund') AND e.revenue_verified IS TRUE
 AND (e.evidence_json->>'provenance'='provider_verified_payment_status' OR e.metadata_json->>'provenance'='provider_verified_payment_status'
 OR e.evidence_json->'provider_status_verified'='true'::jsonb OR e.metadata_json->'provider_status_verified'='true'::jsonb)
 AND COALESCE(e.metadata_json->>'environment','production')='production'
 AND COALESCE(e.evidence_json->>'environment','production')='production'
 AND lower(COALESCE(e.metadata_json->>'simulated','false')) NOT IN ('true','1','yes','on')
 AND lower(COALESCE(e.metadata_json->>'is_simulated','false')) NOT IN ('true','1','yes','on')
 AND lower(COALESCE(e.evidence_json->>'simulated','false')) NOT IN ('true','1','yes','on')
 AND lower(COALESCE(e.evidence_json->>'is_simulated','false')) NOT IN ('true','1','yes','on')
 AND e.provider IN ('mercado_pago','stripe','paypal','asaas','pagarme','pagseguro','efi','adyen')
 AND length(trim(e.payment_id))>0 AND e.value>=0 AND e.value<'1000000000000000'::numeric AND e.currency~'^[A-Z]{3}$'
 UNION ALL
 SELECT e.contact_id,e.occurred_at,e.provider,e.payment_id,e.amount,e.currency,
 CASE WHEN e.payment_status='refunded' THEN 'refund' ELSE 'purchase' END,0
 FROM leoft_payment_events e JOIN tenant_leads l ON e.contact_id=l.id AND e.company_id=l.empresa_id
 WHERE e.occurred_at<=CAST(:observed AS timestamptz) AND e.environment='production' AND e.payment_status IN ('confirmed','refunded')
 AND (e.metadata->>'provenance'='provider_verified_payment_status' OR e.metadata->'provider_status_verified'='true'::jsonb)
 AND lower(COALESCE(e.metadata->>'simulated','false')) NOT IN ('true','1','yes','on')
 AND lower(COALESCE(e.metadata->>'is_simulated','false')) NOT IN ('true','1','yes','on')
 AND e.provider IN ('mercado_pago','stripe','paypal','asaas','pagarme','pagseguro','efi','adyen')
 AND length(trim(e.payment_id))>0 AND e.amount>=0 AND e.amount<'1000000000000000'::numeric AND e.currency~'^[A-Z]{3}$'
), financial_ambiguous AS MATERIALIZED (
 SELECT DISTINCT f.provider,f.payment_id FROM financial_raw f
 WHERE EXISTS (
 SELECT 1 FROM financial_raw conflict
 WHERE conflict.provider=f.provider AND conflict.payment_id=f.payment_id
 AND (conflict.lead_id<>f.lead_id OR (conflict.kind=f.kind AND (conflict.amount<>f.amount OR conflict.currency<>f.currency)))
 )
), financial AS MATERIALIZED (
 SELECT DISTINCT ON (f.provider,f.payment_id,f.kind) f.* FROM financial_raw f
 JOIN cohort l ON l.id=f.lead_id
 WHERE NOT EXISTS (SELECT 1 FROM financial_ambiguous conflict WHERE conflict.provider=f.provider AND conflict.payment_id=f.payment_id)
 ORDER BY f.provider,f.payment_id,f.kind,f.priority,f.occurred_at,f.lead_id
), tag_facts AS MATERIALIZED (
 SELECT e.lead_id,e.occurred_at AT TIME ZONE 'UTC' AS at,m.key,'tag:'||e.id AS fact_id
 FROM crm_tag_events e JOIN cohort l ON e.lead_id=l.id AND e.empresa_id=l.empresa_id
 JOIN tags_crm t ON t.id=e.tag_id AND t.empresa_id=e.empresa_id JOIN tag_mapping m ON m.tag_id=t.id
 JOIN configured s ON s.key=m.key
 WHERE e.action IN ('applied','apply') AND e.occurred_at<=(CAST(:observed AS timestamptz) AT TIME ZONE 'UTC') AND COALESCE(s.meta_event,'') NOT IN ('Purchase','Refund') AND s.key<>'purchase_confirmed'
), stage_facts AS MATERIALIZED (
 SELECT * FROM tag_facts
 UNION ALL
 SELECT e.lead_id,e.occurred_at,s.key,'commercial:'||e.id
 FROM commercial_events e JOIN cohort l ON l.id=e.lead_id AND l.empresa_id=e.empresa_id
 JOIN configured s ON (e.funnel_stage_id=s.key OR (e.funnel_stage_id IS NULL AND e.event_name=s.meta_event))
 WHERE e.occurred_at<=CAST(:observed AS timestamptz) AND e.event_name NOT IN ('Purchase','Refund')
 AND NOT EXISTS (SELECT 1 FROM crm_tag_events t WHERE t.id=e.tag_event_id AND t.empresa_id=e.empresa_id AND t.lead_id=e.lead_id)
 AND NOT EXISTS (SELECT 1 FROM attribution_touches t WHERE t.id=e.attribution_touch_id AND t.empresa_id=e.empresa_id AND t.lead_id=e.lead_id
 AND t.event_kind='checkout_started' AND t.touch_source='checkout_service' AND t.metadata_json->>'provenance'='provider_checkout_reference'
 AND length(trim(t.checkout_id))>0 AND t.occurred_at<=CAST(:observed AS timestamptz) AND s.meta_event='InitiateCheckout')
 AND s.key<>'purchase_confirmed' AND COALESCE(s.meta_event,'') NOT IN ('Purchase','Refund')
 UNION ALL
 SELECT t.lead_id,t.occurred_at,s.key,'checkout:'||t.id
 FROM attribution_touches t JOIN cohort l ON t.lead_id=l.id AND t.empresa_id=l.empresa_id
 JOIN configured s ON s.meta_event='InitiateCheckout'
 WHERE t.event_kind='checkout_started' AND t.touch_source='checkout_service' AND t.metadata_json->>'provenance'='provider_checkout_reference'
 AND length(trim(t.checkout_id))>0 AND t.occurred_at<=CAST(:observed AS timestamptz)
 UNION ALL
 SELECT f.lead_id,f.occurred_at,s.key,'payment:'||f.provider||':'||f.payment_id FROM financial f
 JOIN configured s ON (f.kind='purchase' AND (s.meta_event='Purchase' OR s.key='purchase_confirmed')) OR (f.kind='refund' AND s.meta_event='Refund')
), latest_stage AS (
 SELECT DISTINCT ON (lead_id) lead_id,key,at FROM stage_facts ORDER BY lead_id,at DESC,fact_id DESC
), activity AS (
 SELECT lead_id,max(at) AS last_activity_at FROM (
 SELECT lead_id,criado_em AT TIME ZONE 'UTC' AS at FROM messages
 UNION ALL SELECT lead_id,last_message_at AT TIME ZONE 'UTC' FROM conversations WHERE last_message_at<=(CAST(:observed AS timestamptz) AT TIME ZONE 'UTC')
 ) q GROUP BY lead_id
), crm AS (
 SELECT l.id AS lead_id,e.id,e.nome,e.tipo FROM cohort l JOIN crm_etapas e ON e.id=l.etapa_id JOIN crm_funis f ON f.id=e.funil_id AND f.empresa_id=l.empresa_id
), lead_state AS MATERIALIZED (
 SELECT l.id AS lead_id,l.nome_contato AS display_name,l.entry_at,l.empresa_id,l.status_atendimento,a.last_activity_at,s.key AS current_stage_key,s.at AS stage_at,
 CASE WHEN EXISTS(SELECT 1 FROM financial f WHERE f.lead_id=l.id AND f.kind='purchase') THEN 'converted'
 WHEN c.tipo='lost' THEN 'lost' WHEN l.status_atendimento IN ('fechado','closed','encerrado') OR a.last_activity_at IS NULL THEN 'indeterminate'
 WHEN a.last_activity_at<=CAST(:observed AS timestamptz)-(:inactivity_hours*interval '1 hour') THEN 'inactive' ELSE 'active' END AS status,
 CASE WHEN EXISTS(SELECT 1 FROM acquisitions t WHERE t.lead_id=l.id AND t.exact) THEN 'exact'
 WHEN EXISTS(SELECT 1 FROM acquisitions t WHERE t.lead_id=l.id AND t.resolution_status IN ('resolved','partial')) THEN 'partial' ELSE 'unknown' END AS attribution,
 COALESCE((SELECT array_agg(DISTINCT f.key ORDER BY f.key) FROM stage_facts f WHERE f.lead_id=l.id),ARRAY[]::text[]) AS reached_stage_keys,
 (l.status_atendimento IN ('fechado','closed','encerrado') AND NOT EXISTS(SELECT 1 FROM financial f WHERE f.lead_id=l.id AND f.kind='purchase')) AS closed_without_conversion
 FROM cohort l LEFT JOIN activity a ON a.lead_id=l.id LEFT JOIN latest_stage s ON s.lead_id=l.id LEFT JOIN crm c ON c.lead_id=l.id
), selected AS MATERIALIZED (
 SELECT l.* FROM lead_state l WHERE
 (CAST(:stage AS text) IS NULL OR :stage=ANY(l.reached_stage_keys))
 AND (CAST(:status AS text) IS NULL OR l.status=:status)
 AND (CAST(:origin AS text) IS NULL OR EXISTS(SELECT 1 FROM acquisitions t WHERE t.lead_id=l.lead_id AND (t.channel=:origin OR t.event_kind=:origin))
 OR EXISTS(SELECT 1 FROM conversations c WHERE c.lead_id=l.lead_id AND c.channel=:origin))
 AND (CAST(:campaign_id AS text) IS NULL OR EXISTS(SELECT 1 FROM acquisitions t WHERE t.lead_id=l.lead_id AND t.campaign_id=:campaign_id))
 AND (CAST(:ad_id AS text) IS NULL OR EXISTS(SELECT 1 FROM acquisitions t WHERE t.lead_id=l.lead_id AND t.ad_id=:ad_id))
 AND (CAST(:bottleneck AS text) IS NULL OR (l.status='inactive' AND l.current_stage_key=substring(:bottleneck from 10)))
), ordered_facts AS (
 SELECT f.*,lag(f.key) OVER(PARTITION BY f.lead_id ORDER BY f.at,f.fact_id) AS previous_key FROM stage_facts f JOIN selected l ON l.lead_id=f.lead_id
), observed_transitions AS (
 SELECT f.lead_id,f.previous_key AS from_stage,f.key AS to_stage,
 CASE WHEN b."order"<a."order" THEN 'return' WHEN b."order">a."order"+1 THEN 'skip' ELSE 'advance' END AS kind
 FROM ordered_facts f JOIN configured a ON a.key=f.previous_key JOIN configured b ON b.key=f.key WHERE f.previous_key<>f.key
), followup_sent AS MATERIALIZED (
 SELECT f.lead_id,f.conversation_id,f.sent_at AT TIME ZONE 'UTC' AS sent_at
 FROM followup_executions f JOIN selected l ON l.lead_id=f.lead_id AND l.empresa_id=f.empresa_id
 JOIN channel_conversations c ON c.id=f.conversation_id AND c.empresa_id=f.empresa_id AND c.lead_id=f.lead_id
 JOIN config_followups cfg ON cfg.id=f.followup_id AND cfg.empresa_id=f.empresa_id
 WHERE f.sent_at<=(CAST(:observed AS timestamptz) AT TIME ZONE 'UTC')
 UNION ALL
 SELECT f.lead_id,NULL::uuid,f.data_envio AT TIME ZONE 'UTC' FROM lead_followup_logs f JOIN selected l ON l.lead_id=f.lead_id
 JOIN config_followups cfg ON cfg.id=f.config_followup_id AND cfg.empresa_id=l.empresa_id
 WHERE f.status_envio='enviado' AND f.data_envio<=(CAST(:observed AS timestamptz) AT TIME ZONE 'UTC')
), followup_counts AS (
 SELECT count(DISTINCT f.lead_id) AS sent_leads,count(DISTINCT f.lead_id) FILTER(WHERE EXISTS(
 SELECT 1 FROM messages m JOIN channel_conversations c ON c.id=m.channel_conversation_id AND c.empresa_id=m.empresa_id AND c.lead_id=m.lead_id
 WHERE m.lead_id=f.lead_id AND m.normalized_direction='inbound' AND f.conversation_id IS NOT NULL AND m.channel_conversation_id=f.conversation_id
 AND m.criado_em AT TIME ZONE 'UTC'>f.sent_at AND m.criado_em AT TIME ZONE 'UTC'<=f.sent_at+(:return_window_days*interval '1 day')
 )) AS returned_leads FROM followup_sent f
)
"""

_AGGREGATE = r"""
SELECT jsonb_build_object(
 'summary',(SELECT jsonb_build_object('total_leads',count(*),'conversations',(SELECT count(*) FROM conversations c JOIN selected l ON l.lead_id=c.lead_id),
 'inbound_leads',count(*) FILTER(WHERE EXISTS(SELECT 1 FROM messages m WHERE m.lead_id=l.lead_id AND m.normalized_direction='inbound')),
 'active',count(*) FILTER(WHERE status='active'),'inactive',count(*) FILTER(WHERE status='inactive'),'converted',count(*) FILTER(WHERE status='converted'),
 'lost',count(*) FILTER(WHERE status='lost'),'indeterminate',count(*) FILTER(WHERE status='indeterminate'),
 'closed_without_conversion',count(*) FILTER(WHERE closed_without_conversion)) FROM selected l),
 'stages',COALESCE((SELECT jsonb_agg(q ORDER BY q."order",q.key) FROM (
 SELECT s.key,s.label,s."order",s.source,(SELECT count(DISTINCT f.lead_id) FROM stage_facts f JOIN selected l ON l.lead_id=f.lead_id WHERE f.key=s.key) AS reached_leads,
 (SELECT count(*) FROM selected l WHERE l.current_stage_key=s.key) AS current_leads,
 (SELECT count(*) FROM tag_mapping m WHERE m.key=s.key) AS instrumented_tags,
 (s.meta_event IN ('Purchase','Refund') OR s.key='purchase_confirmed') AS financial_stage FROM configured s) q),'[]'::jsonb),
 'transitions',COALESCE((SELECT jsonb_agg(q ORDER BY q.from_stage,q.to_stage,q.kind) FROM (
 SELECT from_stage,to_stage,kind,count(DISTINCT lead_id) AS lead_count,count(*) AS occurrences FROM observed_transitions GROUP BY from_stage,to_stage,kind) q),'[]'::jsonb),
 'bottlenecks',COALESCE((SELECT jsonb_agg(q ORDER BY q.count DESC,q.stage_key) FROM (
 SELECT l.current_stage_key AS stage_key,count(*) AS count,
 min(extract(epoch FROM CAST(:observed AS timestamptz)-l.stage_at)/3600) AS min,
 percentile_cont(0.5) WITHIN GROUP(ORDER BY extract(epoch FROM CAST(:observed AS timestamptz)-l.stage_at)/3600) AS median,
 max(extract(epoch FROM CAST(:observed AS timestamptz)-l.stage_at)/3600) AS max
 FROM selected l WHERE status='inactive' AND current_stage_key IS NOT NULL GROUP BY current_stage_key) q),'[]'::jsonb),
 'series',COALESCE((SELECT jsonb_agg(q ORDER BY q.period) FROM (
 SELECT to_char(entry_at AT TIME ZONE 'UTC','YYYY-MM-DD') AS period,count(*) AS leads,count(*) FILTER(WHERE status='active') AS active,
 count(*) FILTER(WHERE status='inactive') AS inactive,count(*) FILTER(WHERE status='converted') AS converted,count(*) FILTER(WHERE status='indeterminate') AS indeterminate
 FROM selected GROUP BY 1) q),'[]'::jsonb),
 'crm_snapshot',COALESCE((SELECT jsonb_agg(q ORDER BY q.key) FROM (
 SELECT COALESCE(c.id::text,'unknown') AS key,COALESCE(c.nome,'Sem etapa CRM comprovada') AS label,count(*) AS current_leads
 FROM selected l LEFT JOIN crm c ON c.lead_id=l.lead_id GROUP BY c.id,c.nome) q),'[]'::jsonb),
 'followup',(SELECT to_jsonb(f) FROM followup_counts f),
 'attribution',(SELECT jsonb_build_object('exact_leads',count(*) FILTER(WHERE attribution='exact'),'partial_leads',count(*) FILTER(WHERE attribution='partial'),
 'unknown_leads',count(*) FILTER(WHERE attribution='unknown'),
 'before_entry_leads',count(*) FILTER(WHERE EXISTS(SELECT 1 FROM acquisitions t WHERE t.lead_id=l.lead_id AND t.occurred_at<l.entry_at)),
 'after_entry_leads',count(*) FILTER(WHERE EXISTS(SELECT 1 FROM acquisitions t WHERE t.lead_id=l.lead_id AND t.occurred_at>l.entry_at)),
 'same_time_leads',count(*) FILTER(WHERE EXISTS(SELECT 1 FROM acquisitions t WHERE t.lead_id=l.lead_id AND t.occurred_at=l.entry_at))) FROM selected l),
 'financial',COALESCE((SELECT jsonb_agg(q ORDER BY q.currency) FROM (
 SELECT f.currency,(sum(f.amount) FILTER(WHERE kind='purchase'))::text AS gross,(sum(f.amount) FILTER(WHERE kind='refund'))::text AS refunded,
 count(*) FILTER(WHERE kind='purchase') AS payments FROM financial f JOIN selected l ON l.lead_id=f.lead_id GROUP BY f.currency) q),'[]'::jsonb),
 'unverified_purchase_leads',(SELECT count(DISTINCT e.lead_id) FROM crm_tag_events e JOIN selected l ON l.lead_id=e.lead_id AND l.empresa_id=e.empresa_id
 JOIN tag_mapping m ON m.tag_id=e.tag_id JOIN configured s ON s.key=m.key
 WHERE e.action IN ('applied','apply') AND (s.key='purchase_confirmed' OR s.meta_event='Purchase')
 AND e.occurred_at<=(CAST(:observed AS timestamptz) AT TIME ZONE 'UTC')
 AND NOT EXISTS(SELECT 1 FROM financial f WHERE f.lead_id=e.lead_id AND f.kind='purchase')),
 'stage_history_leads',(SELECT count(*) FROM selected WHERE cardinality(reached_stage_keys)>0),
 'stage_facts_before_entry',(SELECT count(*) FROM stage_facts f JOIN selected l ON l.lead_id=f.lead_id WHERE f.at<l.entry_at),
 'financial_ambiguous_identities',(SELECT count(*) FROM financial_ambiguous ambiguous WHERE EXISTS(
 SELECT 1 FROM financial_raw f JOIN selected l ON l.lead_id=f.lead_id WHERE f.provider=ambiguous.provider AND f.payment_id=ambiguous.payment_id)),
 'entry_missing',(SELECT count(*) FROM tenant_leads l WHERE NOT EXISTS(SELECT 1 FROM entries e WHERE e.lead_id=l.id)),
 'options',jsonb_build_object(
 'origins',COALESCE((SELECT jsonb_agg(q ORDER BY q.key) FROM (SELECT DISTINCT event_kind AS key,event_kind AS label FROM acquisitions UNION SELECT DISTINCT channel AS key,channel AS label FROM acquisitions WHERE channel IN ('whatsapp','instagram','web','sms','telegram','unknown') UNION SELECT DISTINCT channel,channel FROM conversations WHERE channel IN ('whatsapp','instagram','web','sms','telegram','unknown')) q),'[]'::jsonb),
 'campaigns',COALESCE((SELECT jsonb_agg(q ORDER BY q.id) FROM (SELECT DISTINCT campaign_id AS id,'Campanha '||campaign_id AS label FROM acquisitions WHERE campaign_id IS NOT NULL) q),'[]'::jsonb),
 'ads',COALESCE((SELECT jsonb_agg(q ORDER BY q.id) FROM (SELECT DISTINCT ad_id AS id,'Anúncio '||ad_id AS label FROM acquisitions WHERE ad_id IS NOT NULL) q),'[]'::jsonb))
)
"""

_ADS = r"""SELECT ad_account_id,account_currency,entity_id AS ad_id,campaign_id,date_start,date_stop,attribution_setting,
 spend,impressions,clicks,messaging_conversations FROM meta_ads_insights_daily
 WHERE empresa_id=CAST(:tenant AS uuid) AND entity_level='ad' AND entity_id~'^[0-9]{1,40}$' AND ad_account_id~'^(act_)?[0-9]{1,40}$'
 AND date_start>=CAST(:start AS timestamptz)::date AND date_stop<CAST(:end AS timestamptz)::date
 AND date_stop<=CAST(:observed AS timestamptz)::date AND fetched_at<=CAST(:observed AS timestamptz)
 AND (CAST(:campaign_id AS text) IS NULL OR campaign_id=:campaign_id)
 AND (CAST(:ad_id AS text) IS NULL OR ad_id=:ad_id)
 ORDER BY ad_account_id,date_start,entity_id,attribution_setting LIMIT 501"""


async def dashboard(session, *, empresa_id, **kwargs):
    p = filters(empresa_id=empresa_id, **kwargs)
    configured = await _configuration(session,p)
    data = (await session.execute(text(_CTE+_AGGREGATE),p)).scalar_one()
    total = data['summary']['total_leads']
    financial = data.pop('financial')
    for item in financial:
        item['net'] = str(Decimal(item['gross'])-Decimal(item['refunded'] or '0')) if item['gross'] is not None else None
        item['amount'] = item['net']
    data['summary']['revenue'] = {'status':'observed' if financial else 'unknown','by_currency':financial}
    for stage in data['stages']:
        instrumented = stage.pop('instrumented_tags')>0 or stage['reached_leads']>0
        financial_stage = stage.pop('financial_stage')
        if financial_stage:
            stage['confirmed_reached_leads'] = stage['reached_leads']
        if not instrumented or financial_stage and stage['reached_leads'] == 0:
            stage['reached_leads'] = None
        stage['state'] = 'observed' if stage['reached_leads'] is not None else 'unknown'
        stage['coverage'] = {'state':'partial','denominator':total,'observed_leads':stage['reached_leads']}
        stage['rate'] = _rate(stage['reached_leads'],total)
    reached = {s['key']:s['reached_leads'] for s in data['stages']}
    labels = {s['key']:s['label'] for s in data['stages']}
    for transition in data['transitions']:
        transition['rate'] = _rate(transition['lead_count'],reached.get(transition['from_stage']))
    for item in data['bottlenecks']:
        item['key'] = 'inactive:'+item['stage_key']
        item['title'] = 'Inativos sem avanço posterior observado: '+labels[item['stage_key']]
        item['denominator'] = reached.get(item['stage_key'])
        item['rate'] = _rate(item['count'],item['denominator'])
        item['duration_hours'] = {key:item.pop(key) for key in ('min','median','max')}
        item['coverage'] = {'state':'partial','denominator':total,'observed_leads':data['stage_history_leads']}
    followup = data['followup']
    followup.update(numerator=followup['returned_leads'],denominator=followup['sent_leads'],rate=_rate(followup['returned_leads'],followup['sent_leads']),
                    window_days=p['return_window_days'],relation='temporal_same_conversation_not_causal')
    attribution = data['attribution']
    temporal = {key:attribution.pop(key) for key in ('before_entry_leads','after_entry_leads','same_time_leads')}
    attribution.update(denominator=total,coverage=_rate(attribution['exact_leads'],total)|temporal)
    data['coverage'] = {
        'entry':{'state':'observed','eligible_leads':total,'without_eligible_entry':data.pop('entry_missing'),'definition':_window(p)['entry_definition']},
        'stage_history':{'state':'partial','observed_leads':data.pop('stage_history_leads'),'denominator':total,'crm_snapshot_is_not_history':True,'current_stage_definition':'last_observed_stage_fact_not_crm_snapshot','tag_removal_does_not_invent_reverse_transition':True,'facts_before_entry':data.pop('stage_facts_before_entry')},
        'financial':{'state':'observed' if financial else 'unknown','confirmed_leads':data['summary']['converted'],'unverified_purchase_signal_leads':data.pop('unverified_purchase_leads'),'ambiguous_payment_identities':data.pop('financial_ambiguous_identities')},
        'loss':{'state':'partial' if data['summary']['lost'] else 'not_instrumented','reason':None,'closed_is_not_lost':True},
        'prompt_exposure':{'state':'not_instrumented'},'limitations':LIMITATIONS,
    }
    if data['summary']['lost'] == 0:
        data['summary']['lost'] = None
    for item in data['crm_snapshot']:
        item['label'] = _label(item['label']) or 'Etapa CRM'
    data['options'].update(stages=[{'key':s['key'],'label':s['label']} for s in configured],statuses=[{'key':s,'label':s} for s in STATUSES])
    rows = (await session.execute(text(_ADS),p)).mappings().all()
    ads = []
    for row in rows[:500]:
        item = dict(row)
        for field in ('date_start','date_stop'):
            item[field] = item[field].isoformat()
        for field in ('spend','messaging_conversations'):
            if item[field] is not None:
                item[field] = str(item[field])
        ads.append(item)
    data['ads_comparison'] = {'state':'observed' if ads else 'no_event','level':'ad','rows':ads,
        'limitations':['Snapshots Meta separados: conta/moeda/janela/configuração de atribuição não são somados entre níveis.',
                       'Cliques e conversas Meta não equivalem a contatos locais. Sem ROAS inferido.'], 'truncated':len(rows)>500}
    data['window'] = _window(p)
    return data


async def drilldown(session, *, empresa_id, **kwargs):
    p = filters(empresa_id=empresa_id,**kwargs)
    await _configuration(session,p)
    query = _CTE+"""SELECT (SELECT count(*) FROM selected) AS total,
 COALESCE((SELECT jsonb_agg(q ORDER BY q.entry_at DESC,q.lead_id) FROM (
 SELECT l.lead_id,l.display_name,l.entry_at,l.last_activity_at,l.status,l.current_stage_key,s.label AS current_stage_label,l.attribution,l.reached_stage_keys,
 '/painel/inbox?lead='||l.lead_id||'&journey=1' AS journey_path
 FROM selected l LEFT JOIN configured s ON s.key=l.current_stage_key ORDER BY l.entry_at DESC,l.lead_id LIMIT :limit OFFSET :offset) q),'[]'::jsonb) AS items"""
    row = (await session.execute(text(query),p)).mappings().one()
    return {'items':row['items'],'total':row['total'],'limit':p['limit'],'offset':p['offset'],'window':_window(p)}


async def export_csv(session, *, empresa_id, **kwargs):
    data = await dashboard(session,empresa_id=empresa_id,**kwargs)
    stream = io.StringIO(newline='')
    writer = _AggregateCSVWriter(stream)
    writer.writerow(['section','metric','value','numerator','denominator','state','currency'])
    # No tenant/contact/conversation identifiers, names, labels or raw metadata.
    for key in ('total_leads','conversations','inbound_leads',*STATUSES,'closed_without_conversion'):
        writer.writerow(['summary',key,data['summary'][key],'',data['summary']['total_leads'],
                         'unknown' if data['summary'][key] is None else 'observed',''])
    for item in data['stages']:
        writer.writerow(['stage',_export_key(item['key']),item['reached_leads'] if item['reached_leads'] is not None else '',
                         item['rate']['numerator'] if item['rate']['numerator'] is not None else '',item['rate']['denominator'],item['state'],''])
    for item in data['transitions']:
        writer.writerow(['transition',_export_key(item['from_stage'])+'>'+_export_key(item['to_stage']),item['lead_count'],item['rate']['numerator'],item['rate']['denominator'],item['kind'],''])
    for item in data['bottlenecks']:
        writer.writerow(['bottleneck',_export_key(item['stage_key']),item['count'],item['count'],item['denominator'],'inactive',''])
    writer.writerow(['followup','returned',data['followup']['returned_leads'],data['followup']['numerator'],data['followup']['denominator'],'temporal',''])
    for key in ('exact_leads','partial_leads','unknown_leads'):
        writer.writerow(['attribution',key,data['attribution'][key],data['attribution'][key],data['attribution']['denominator'],'observed',''])
    for item in data['summary']['revenue']['by_currency']:
        for key in ('gross','refunded','net'):
            writer.writerow(['revenue',key,item[key] if item[key] is not None else '', '', '', 'observed' if item[key] is not None else 'unknown',item['currency']])
    if not data['summary']['revenue']['by_currency']:
        writer.writerow(['revenue','net','','','','unknown',''])
    return stream.getvalue()


class _AggregateCSVWriter:
    def __init__(self, stream):
        self.writer = csv.writer(stream)

    def writerow(self, values):
        # Spreadsheet formula guards remain necessary even with a fixed allowlist.
        self.writer.writerow(["'"+value if isinstance(value,str) and value.startswith(('=','+','-','@','\t','\r')) else value for value in values])


def _export_key(value):
    # Stable distinct aliases retain stage identity without exporting raw UUIDs.
    return 'legacy_stage_'+hashlib.sha256(value.encode('utf-8')).hexdigest()[:16] if value.startswith('tag:') else value


commercial_funnel_dashboard = dashboard
commercial_funnel_leads = drilldown

"""Read-only SHADOW orchestration. Customer text is never persisted in the ledger."""
from __future__ import annotations
import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from sqlalchemy import select, func, or_, and_
from app.services.jev_classification import SCHEMA_VERSION, MODEL, demo_classify, empty_classification, suggest_tags
from app.services.jev_examples import examples
from app.services.jev_gateway import evaluate, GatewayError
from app.services.jev_ledger import JevLedger, LedgerUnavailable, new_client, MAX_RUNS
from db.models import CRMLead, MensagemHistorico, TagCRM, Empresa, ChannelConversation

class NotFound(Exception):
    pass


def shadow_enabled():
    return os.getenv('JEV_SHADOW_ENABLED', 'true').strip().lower() in ('true', '1', 'yes')


def real_authorized(tenant):
    return os.getenv('JEV_REAL_AUTHORIZED_' + str(tenant).replace('-', '').upper(), '').strip().lower() in ('true', '1', 'yes')


async def configuration(db, tenant):
    from app.services.jev_config import load_config
    return await load_config(db, tenant)


def configured(config):
    return bool(config.enabled and config.api_key and config.model == MODEL and config.provider == 'typesafe' and config.daily_budget_usd > 0)


def redact_direct_identifiers(text):
    # Privacy authorization remains mandatory: this does not claim complete anonymization.
    text = re.sub(r'\b[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}\b', '[EMAIL]', str(text))
    text = re.sub(r'https?://\S+|www\.\S+', '[URL]', text)
    text = re.sub(r'(?<!\w)\+?\d[\d\s().-]{6,}\d(?!\w)', '[NUMBER]', text)
    return text


async def read_context(db, tenant, lead_id):
    parent = await db.scalar(select(CRMLead.id).where(CRMLead.empresa_id == tenant, CRMLead.id == lead_id))
    if parent is None:
        raise NotFound('lead_not_found')
    m = MensagemHistorico
    c = ChannelConversation
    eligible = [m.empresa_id == tenant, m.lead_id == lead_id, m.tipo_mensagem == 'text',
                func.lower(func.coalesce(m.metadata_json['event_kind'].astext, '')).notin_(('reaction', 'status')),
                or_(m.direction.in_(('inbound', 'outbound')), and_(m.direction.is_(None), m.from_me.is_not(None))),
                or_(m.channel_conversation_id.is_(None), c.id.is_not(None))]
    # The latest eligible message selects one conversation. A legacy NULL conversation
    # selects only legacy messages for this scoped lead; distinct channels never mix.
    selected = await db.scalar(select(m.channel_conversation_id)
          .outerjoin(c, and_(c.id == m.channel_conversation_id, c.empresa_id == tenant, c.lead_id == lead_id))
          .where(*eligible).order_by(m.criado_em.desc(), m.id.desc()).limit(1))
    rows = (await db.execute(select(m.id, m.texto, m.from_me, m.direction)
          .outerjoin(c, and_(c.id == m.channel_conversation_id, c.empresa_id == tenant, c.lead_id == lead_id))
          .where(*eligible, m.channel_conversation_id == selected if selected is not None else m.channel_conversation_id.is_(None))
          .order_by(m.criado_em.desc(), m.id.desc()).limit(8))).all()
    messages = []
    remaining = 4000
    for row in rows:
        if row.texto is None:
            continue
        if row.direction in ('inbound', 'outbound'):
            role = 'agent' if row.direction == 'outbound' else 'customer'
        elif row.direction is None and isinstance(row.from_me, bool):
            role = 'agent' if row.from_me else 'customer'
        else:
            continue
        text = redact_direct_identifiers(row.texto)[:min(800, remaining)]
        if not text:
            continue
        remaining -= len(text)
        messages.append({'id': str(row.id), 'text': text, 'role': role})
        if remaining <= 0:
            break
    return list(reversed(messages))


async def read_tags(db, tenant):
    rows = (await db.execute(select(TagCRM.id, TagCRM.nome, TagCRM.instrucao_ia).where(TagCRM.empresa_id == tenant).order_by(TagCRM.id).limit(200))).all()
    tags = []
    for row in rows:
        metadata = {}
        instruction = str(row.instrucao_ia or '')
        if '[AGENTEOS_MONITOR_FUNNEL]' in instruction:
            try:
                metadata = json.loads(instruction.split('[AGENTEOS_MONITOR_FUNNEL]', 1)[1].strip())
            except (ValueError, TypeError):
                pass
        metadata = metadata if isinstance(metadata, dict) else {}
        managed = metadata.get('managed_by_monitor_funnel') is True
        tags.append({'id': str(row.id), 'name': str(row.nome or '')[:100],
                     'managed_stage': metadata.get('funnel_stage_key') if managed else None,
                     'managed_version': metadata.get('funnel_version') if managed else None})
    spec = await db.scalar(select(Empresa.credenciais_canais['monitor_funnel_spec']).where(Empresa.id == tenant))
    spec = spec if isinstance(spec, dict) else {}
    # Managed tags whose snapshot versions disagree cannot be suggestions.
    tags = [tag for tag in tags if not tag['managed_stage'] or tag['managed_version'] == spec.get('version')]
    return tags, {'version': spec.get('version'), 'stages': spec.get('stages', []) if isinstance(spec.get('stages'), list) else []}


def new_run(source, lead_id=None):
    return {'schema_version': SCHEMA_VERSION, 'id': str(uuid.uuid4()), 'source': source, 'mode': 'shadow',
            'status': 'not_evaluated', 'created_at': datetime.now(timezone.utc).isoformat(), 'lead_id': str(lead_id) if lead_id else None,
            'classification': empty_classification('not_evaluated'), 'metrics': {'latency_ms': None, 'input_tokens': None, 'cost_usd': None, 'cost_status': 'unknown'},
            'deduplicated': False, 'limitations': ['Contexto recente limitado; histórico não completo.', 'Sugestões hipotéticas; nenhuma tag ou ação aplicada.',
            'Seleção da conversa da mensagem elegível mais recente; canais não são misturados.',
            'Perguntas sem resposta e hipótese de interrupção permanecem desconhecidas no contexto limitado.']}


async def analyze(db, tenant, *, example_id=None, lead_id=None, use_provider=False, ledger=None):
    source = 'synthetic' if example_id else 'real'
    run = new_run(source, lead_id)
    if example_id:
        example = next((item for item in examples() if item['id'] == example_id), None)
        if example is None:
            raise NotFound('example_not_found')
        messages = example['messages']
        run['example_id'] = example_id
    else:
        # Scope check even when provider disabled. This never leaks existence across tenants.
        messages = await read_context(db, tenant, lead_id)
    tags, spec = await read_tags(db, tenant)
    config = await configuration(db, tenant)
    owns_client = ledger is None
    client = new_client() if owns_client else None
    ledger = ledger or JevLedger(client, tenant)
    reservation = False
    try:
        if not shadow_enabled():
            run['status'] = 'disabled'
        elif source == 'synthetic' and not use_provider:
            run['status'] = 'demo'
            run['engine'] = 'local_demo'
            run['classification'] = suggest_tags(demo_classify(messages), tags, spec, synthetic=True)
            run['metrics']['cost_status'] = 'not_applicable_local_demo'
        elif not configured(config):
            run['status'] = 'not_configured'
        elif source == 'real' and not real_authorized(tenant):
            run['status'] = 'privacy_blocked'
        elif not any(m['role'] == 'customer' for m in messages):
            run['status'] = 'not_evaluated'
            run['error_code'] = 'no_customer_context'
        else:
            digest_input = {'schema': SCHEMA_VERSION, 'model': MODEL, 'config_version': getattr(config, 'version', 0), 'budget_day': datetime.now(timezone.utc).date().isoformat(), 'source': source, 'lead_id': str(lead_id or ''), 'example': example_id,
                            'messages': messages, 'spec': spec, 'tags': tags}
            digest = hashlib.sha256(json.dumps(digest_input, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
            decision, existing_id = await ledger.reserve(digest, run['id'], int(config.daily_budget_usd * Decimal(1_000_000_000)))
            if decision == 'duplicate':
                existing = await ledger.get(existing_id)
                if existing:
                    existing['deduplicated'] = True
                    return existing
                run['status'] = 'in_progress'
                run['error_code'] = 'context_in_progress_or_expired'
            elif decision != 'reserved':
                run['status'] = decision
            else:
                reservation = True
                classification, metrics = await evaluate(config, messages, ledger)
                run['status'] = 'completed'
                run['engine'] = 'typesafe'
                run['classification'] = suggest_tags(classification, tags, spec, synthetic=source == 'synthetic')
                run['metrics'] = metrics
                if source == 'synthetic':
                    for item in run['classification']['evidence']:
                        item['excerpt'] = next(m['text'][:180] for m in messages if m['id'] == item['message_id'])
        if run['status'] not in ('demo', 'completed'):
            run['classification'] = empty_classification(run.get('error_code') or run['status'])
        try:
            await ledger.save(run)
            run['ledger_persisted'] = True
        except LedgerUnavailable:
            run['ledger_persisted'] = False
            run['ledger_error'] = 'redis_unavailable'
        return run
    except LedgerUnavailable:
        run['status'] = 'ledger_unavailable'
        run['error_code'] = 'redis_unavailable'
        run['classification'] = empty_classification('ledger_unavailable')
        return run
    except GatewayError as exc:
        run['status'] = 'budget_exceeded' if exc.code == 'budget_exceeded' else 'provider_error'
        run['error_code'] = exc.code
        run['metrics']['latency_ms'] = exc.latency_ms
        run['classification'] = empty_classification(exc.code)
        try:
            await ledger.save(run)
            run['ledger_persisted'] = True
        except LedgerUnavailable:
            run['ledger_persisted'] = False
        return run
    finally:
        if reservation:
            try:
                await ledger.release(run['id'])
            except LedgerUnavailable:
                pass
        if owns_client:
            await client.aclose()


async def status(db, tenant, *, ledger=None):
    config = await configuration(db, tenant)
    owns_client = ledger is None
    client = new_client() if owns_client else None
    ledger = ledger or JevLedger(client, tenant)
    runs = []
    reserved = None
    available = True
    try:
        runs = await ledger.runs()
        reserved = await ledger.reserved()
    except LedgerUnavailable:
        available = False
    finally:
        if owns_client:
            await client.aclose()
    evaluated = [r for r in runs if r['status'] == 'completed']
    real = [r for r in runs if r['source'] == 'real']
    latest_provider = next((r for r in runs if r['status'] in ('completed', 'provider_error')), None)
    measured = [r['metrics'].get('cost_usd') for r in evaluated if r['metrics'].get('cost_usd') is not None and r['created_at'][:10] == datetime.now(timezone.utc).date().isoformat()]
    return {'schema_version': SCHEMA_VERSION, 'mode': 'shadow', 'enabled': shadow_enabled(),
            'provider': {'configured': configured(config), 'enabled': bool(config.enabled), 'authorized_real': real_authorized(tenant), 'model': MODEL,
                         'availability': 'not_configured' if not configured(config) else ('observed_success' if latest_provider and latest_provider['status'] == 'completed' else 'observed_error' if latest_provider else 'not_tested'),
                         'last_error': next((r.get('error_code') for r in runs if r['status'] == 'provider_error'), None)},
            'budget': {'daily_usd': float(config.daily_budget_usd), 'reserved_usd': reserved, 'measured_usd': sum(measured) if measured else None,
                       'currency': 'USD', 'reservation_per_attempt_usd': .002688, 'basis': 'documented_64000_input_token_ceiling'},
            'ledger': {'available': available, 'retention_days': 7, 'max_runs': MAX_RUNS},
            'coverage': {'requested': len(runs), 'evaluated': len(evaluated), 'synthetic_demos': sum(r['status'] == 'demo' for r in runs),
                         'real_requested': len(real), 'real_evaluated': sum(r['status'] == 'completed' for r in real),
                         'abstained': sum(r['classification']['abstention']['abstained'] for r in evaluated),
                         'errors': sum(r['status'] in ('provider_error', 'ledger_unavailable', 'budget_exceeded', 'concurrency_limited') for r in runs), 'denominator': 'retained_runs'},
            'limitations': ['Provider OFF por padrão; conversas reais exigem autorização separada por tenant.',
                            'Cobertura representa execuções explícitas retidas, não todos os leads.',
                            'Precisão real não medida; demonstrações são exemplos sintéticos autorais.',
                            'Orçamento UTC reserva o teto documentado por tentativa; reservas incertas não são devolvidas.',
                            'Ledger Redis operacional com retenção limitada; não é auditoria permanente.']}

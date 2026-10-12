"""Opt-in integration around the existing single final generation, never dispatch/tools."""
from __future__ import annotations
import asyncio
import time
import uuid
from datetime import datetime, timezone
from sqlalchemy import select
from app.services.a07_flags import rollout
from app.services.a07_composer import compose, DEADLINE_SECONDS
from app.services.a07_ledger import persist_trace
from app.services import jev_shadow


async def read_primary_stage(db, tenant, lead_id):
    from db.models import CRMLead, CRMEtapa, CRMFunil
    stage = {'value': None, 'status': 'unknown', 'source': 'crm_snapshot'}
    if not lead_id:
        return stage
    try:
        lead_uuid = uuid.UUID(str(lead_id))
    except (ValueError, TypeError):
        return stage
    # Both parent and funnel are scoped. CRMEtapa has no empresa_id of its own.
    row = (await db.execute(select(CRMEtapa.id).select_from(CRMLead)
        .join(CRMEtapa, CRMEtapa.id == CRMLead.etapa_id)
        .join(CRMFunil, CRMFunil.id == CRMEtapa.funil_id)
        .where(CRMLead.empresa_id == tenant, CRMLead.id == lead_uuid, CRMFunil.empresa_id == tenant))).first()
    if row:
        stage = {'value': str(row[0]), 'status': 'known', 'source': 'crm_snapshot'}
    return stage


async def current_jev_context(db, tenant, state):
    """Verify same latest conversation/channel and current inbound before external egress."""
    from db.models import ChannelConversation, MensagemHistorico
    if not state.lead_id:
        return None
    try:
        lead = uuid.UUID(str(state.lead_id))
        conversation_id = uuid.UUID(str(state.metadata.get('conversation_id')))
    except (ValueError, TypeError):
        return None
    conversation = await db.scalar(select(ChannelConversation.id).where(ChannelConversation.id == conversation_id,
        ChannelConversation.empresa_id == tenant, ChannelConversation.lead_id == lead, ChannelConversation.channel == state.canal))
    if conversation is None:
        return None
    # A06 selects the latest eligible conversation; reject if another conversation is newer.
    latest = (await db.execute(select(MensagemHistorico.channel_conversation_id)
        .where(MensagemHistorico.empresa_id == tenant, MensagemHistorico.lead_id == lead, MensagemHistorico.tipo_mensagem == 'text')
        .order_by(MensagemHistorico.criado_em.desc(), MensagemHistorico.id.desc()).limit(1))).first()
    if not latest or latest[0] != conversation_id:
        return None
    messages = await jev_shadow.read_context(db, tenant, lead)
    if not messages or messages[-1]['role'] != 'customer' or messages[-1]['text'] != jev_shadow.redact_direct_identifiers(state.mensagem_atual)[:800]:
        return None
    return messages


async def runtime_classification(db, tenant, state):
    config = await jev_shadow.configuration(db, tenant)
    if not jev_shadow.configured(config) or not jev_shadow.real_authorized(tenant):
        return None, 'local_deterministic', None
    before = await current_jev_context(db, tenant, state)
    if before is None:
        return None, 'local_deterministic', 'context_mismatch'
    # Revalidate immediately at the egress boundary after asynchronous context reads.
    current_config = await jev_shadow.configuration(db, tenant)
    if config.version != current_config.version or not jev_shadow.configured(current_config) or not jev_shadow.real_authorized(tenant) or rollout(tenant, state.canal)['mode'] != 'MODULAR':
        return None, 'local_deterministic', 'configuration_changed'
    run = await jev_shadow.analyze(db, tenant, lead_id=uuid.UUID(str(state.lead_id)))
    after = await current_jev_context(db, tenant, state)
    current_config = await jev_shadow.configuration(db, tenant)
    if after != before or config.version != current_config.version or not jev_shadow.real_authorized(tenant) or rollout(tenant, state.canal)['mode'] != 'MODULAR':
        return None, 'local_deterministic', 'configuration_changed'
    if run.get('status') != 'completed':
        reason = run.get('error_code')
        return None, 'local_deterministic', reason if reason in ('provider_timeout', 'budget_exceeded') else 'provider_error'
    # A06 dedup hashes exact context/config/schema; reuse only its trusted server-owned result.
    if run.get('source') != 'real' or run.get('lead_id') != str(state.lead_id) or run.get('schema_version') != 'a06.v1':
        return None, 'local_deterministic', 'context_mismatch'
    evidence = {e.get('message_id') for e in run.get('classification', {}).get('evidence', [])}
    if not evidence or not evidence.issubset({m['id'] for m in before}):
        return None, 'local_deterministic', 'context_mismatch'
    return run['classification'], 'jev_observed', None


async def prepare_runtime(state, legacy_prompt, *, messages, tools_context):
    from db.database import AsyncSessionLocal
    tenant = uuid.UUID(str(state.empresa_id))
    started = time.monotonic()
    trace = {'run_id': str(uuid.uuid4()), 'created_at': datetime.now(timezone.utc).isoformat(), 'source': 'real',
             'channel': state.canal, 'status': 'prepared', 'measurement_source': 'local_runtime'}
    async def prepare():
        async with AsyncSessionLocal() as db:
            stage = await read_primary_stage(db, tenant, state.lead_id)
            classification, source, reason = await runtime_classification(db, tenant, state)
        if rollout(tenant, state.canal)['mode'] != 'MODULAR':
            reason = 'configuration_changed'
        composition = compose(legacy_prompt, message=state.mensagem_atual, primary_stage=stage,
            classification=classification, classification_source=source, forced_fallback=reason,
            messages=messages, tools_context=tools_context)
        trace.update(composition)
        trace['metrics'] = {'input_tokens': None, 'output_tokens': None, 'cost_usd': None,
            'estimated_input_tokens': composition['estimated_input_tokens'], 'latency_ms': round((time.monotonic() - started) * 1000)}
        persisted = await persist_trace(tenant, trace)
        if not persisted:
            composition['prompt'] = legacy_prompt
            composition['mode'] = 'LEGACY'
            composition['fallback_reason'] = 'ledger_unavailable'
            trace.update(composition)
        trace['persisted'] = persisted
        trace['deadline_remaining'] = max(.001, DEADLINE_SECONDS - (time.monotonic() - started))
        return composition['prompt'], trace
    try:
        return await asyncio.wait_for(prepare(), DEADLINE_SECONDS)
    except asyncio.TimeoutError:
        reason = 'composition_timeout'
    except Exception:
        reason = 'composition_error'
    trace.update(compose(legacy_prompt, message=state.mensagem_atual, forced_fallback=reason, messages=messages, tools_context=tools_context))
    trace['persisted'] = False
    return legacy_prompt, trace


def metadata(trace):
    from app.services.a07_ledger import clean_trace
    return {**clean_trace(trace), 'trace': {'persisted': bool(trace.get('persisted'))}}


async def complete_runtime(state, trace, *, guard_allowed=None, generation_error=None, latency_ms=None):
    if not trace or not trace.get('persisted'):
        return
    trace['guard_allowed'] = guard_allowed
    trace['status'] = generation_error or ('completed' if guard_allowed else 'guard_blocked')
    if generation_error:
        trace['fallback_reason'] = generation_error
    if latency_ms is not None:
        trace.setdefault('metrics', {})['latency_ms'] = latency_ms
    try:
        trace['persisted'] = await asyncio.wait_for(persist_trace(state.empresa_id, trace), 1)
    except Exception:
        trace['persisted'] = False


def capture_generation_usage(trace, provider):
    """Observe only this completed final generation; never infer whole-pipeline cost."""
    from app.agents.llm.openai_provider import OpenAIAgentLLM
    receipt = getattr(provider, 'last_usage_observation', None)
    if not isinstance(receipt, dict):
        return
    measured = {}
    for key in ('input_tokens', 'output_tokens'):
        value = receipt.get(key)
        measured[key] = value if isinstance(value, int) and not isinstance(value, bool) and 0 <= value < 1e9 else None
    if all(value is None for value in measured.values()):
        return
    trace.setdefault('metrics', {}).update(measured)
    trace['metrics']['cost_usd'] = None
    trace['measurement_source'] = 'provider_observed' if isinstance(provider, OpenAIAgentLLM) else 'synthetic_provider_receipt'
    trace['metric_scope'] = 'final_generation_only'

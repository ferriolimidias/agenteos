"""Tenant-scoped bounded audit metadata; never persists prompts/customer text."""
from __future__ import annotations
import json
import uuid
from datetime import datetime
from app.services.jev_ledger import new_client

RETENTION_SECONDS = 7 * 86400
MAX_RUNS = 500
MAX_PAYLOAD_BYTES = 12000
# Remove the old ID from the list before updating a run, so completion doesn't duplicate it.
SAVE = """
redis.call('LREM',KEYS[2],0,ARGV[3])
redis.call('SET',KEYS[1],ARGV[1],'EX',ARGV[2]);redis.call('LPUSH',KEYS[2],ARGV[3])
local removed=redis.call('LRANGE',KEYS[2],ARGV[4],-1)
for _,id in ipairs(removed) do redis.call('DEL',ARGV[5]..id) end
redis.call('LTRIM',KEYS[2],0,tonumber(ARGV[4])-1);redis.call('EXPIRE',KEYS[2],ARGV[2]);return 1
"""


class LedgerUnavailable(Exception):
    pass


def clean_trace(trace):
    from app.services.a07_composer import COMPOSITION_VERSION, SCHEMA_VERSION, MODULES
    # Build nested objects explicitly: unknown keys are never copied.
    modules = []
    for m in trace.get('selected_modules', []):
        if isinstance(m, dict) and m.get('id') in MODULES and m.get('version') == '1.0.0':
            modules.append({'id': m['id'], 'version': '1.0.0'})
    metrics = trace.get('metrics') if isinstance(trace.get('metrics'), dict) else {}
    safe_metrics = {}
    for key in ('input_tokens', 'output_tokens', 'cost_usd', 'estimated_input_tokens', 'latency_ms'):
        v = metrics.get(key)
        if v is None or (not isinstance(v, bool) and isinstance(v, (int, float)) and 0 <= v < 1e12):
            safe_metrics[key] = v
    stage = trace.get('primary_stage') if isinstance(trace.get('primary_stage'), dict) else {}
    try:
        stage_id = str(uuid.UUID(str(stage.get('value')))) if stage.get('source') == 'crm_snapshot' and stage.get('value') else None
    except (ValueError, TypeError):
        stage_id = None
    allowed_reasons = {'untrusted_instruction', 'conflicting_facts', 'human_request_overrides_commercial_offer', 'classification_abstained',
        'invalid_module', 'classification_low_confidence', 'no_reliable_topics', 'module_budget_exceeded', 'input_budget_exceeded',
        'provider_error', 'provider_timeout', 'budget_exceeded', 'ledger_unavailable', 'context_mismatch', 'configuration_changed',
        'first_turn_legacy', 'composition_timeout', 'composition_error', 'guard_blocked', 'completed', 'prepared', 'generation_error', 'generation_timeout'}
    reason = trace.get('fallback_reason')
    try:
        created_at = datetime.fromisoformat(str(trace.get('created_at'))).isoformat()
    except (ValueError, TypeError):
        created_at = None
    return {'schema_version': SCHEMA_VERSION, 'composition_version': COMPOSITION_VERSION,
        'run_id': str(uuid.UUID(str(trace['run_id']))), 'created_at': created_at,
        'source': trace.get('source') if trace.get('source') in ('synthetic', 'real') else 'unknown',
        'channel': trace.get('channel') if trace.get('channel') in ('whatsapp', 'instagram', 'simulator') else 'unknown',
        'selected_modules': modules[:6], 'primary_stage': {'value': stage_id, 'status': 'known' if stage_id else 'unknown', 'source': stage.get('source') if stage.get('source') in ('crm_snapshot', 'synthetic_fixture') else 'unknown'},
        'classification_source': trace.get('classification_source') if trace.get('classification_source') in ('local_deterministic', 'jev_observed') else 'unknown',
        'mode': 'MODULAR' if trace.get('mode') == 'MODULAR' else 'LEGACY',
        'status': trace.get('status') if trace.get('status') in ('prepared', 'completed', 'guard_blocked', 'generation_error', 'generation_timeout') else 'unknown',
        'fallback_reason': reason if reason in allowed_reasons else None, 'guard_allowed': trace.get('guard_allowed') if isinstance(trace.get('guard_allowed'), bool) else None,
        'metrics': safe_metrics, 'metric_scope': 'final_generation_only' if trace.get('metric_scope') == 'final_generation_only' else 'local_composition', 'measurement_source': trace.get('measurement_source') if trace.get('measurement_source') in ('local_synthetic', 'local_runtime', 'provider_observed', 'synthetic_provider_receipt') else 'unknown'}


class A07Ledger:
    def __init__(self, client, tenant):
        self.client = client
        self.prefix = f'a07:{uuid.UUID(str(tenant))}:'

    async def execute(self, method, *args, **kwargs):
        try:
            return await getattr(self.client, method)(*args, **kwargs)
        except Exception as exc:
            raise LedgerUnavailable('redis_unavailable') from exc

    async def available(self):
        return bool(await self.execute('ping'))

    async def save(self, trace):
        safe = clean_trace(trace)
        encoded = json.dumps(safe, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > MAX_PAYLOAD_BYTES:
            raise ValueError('trace_too_large')
        await self.execute('eval', SAVE, 2, self.prefix + 'run:' + safe['run_id'], self.prefix + 'runs',
            encoded, RETENTION_SECONDS, safe['run_id'], MAX_RUNS, self.prefix + 'run:')
        return safe

    async def get(self, run_id):
        key = str(uuid.UUID(str(run_id)))
        raw = await self.execute('get', self.prefix + 'run:' + key)
        return clean_trace(json.loads(raw)) if raw else None

    async def runs(self):
        ids = await self.execute('lrange', self.prefix + 'runs', 0, MAX_RUNS - 1)
        if not ids:
            return []
        rows = await self.execute('mget', [self.prefix + 'run:' + str(uuid.UUID(str(i))) for i in ids])
        return [clean_trace(json.loads(row)) for row in rows if row]


async def persist_trace(tenant, trace):
    client = new_client()
    try:
        await A07Ledger(client, tenant).save(trace)
        return True
    except (LedgerUnavailable, ValueError, TypeError, KeyError):
        return False
    finally:
        await client.aclose()

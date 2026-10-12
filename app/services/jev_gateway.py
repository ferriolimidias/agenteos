"""TypeSafe HTTP boundary; default-off config, no logs of requests or exceptions."""
from __future__ import annotations
import asyncio
import json
import time
import httpx
from app.services.jev_classification import MODEL, provider_classify, questions
from app.services.jev_ledger import ATTEMPT_NANO_USD

ENDPOINT = 'https://api.typesafe.ai/v1/systemone'
PRICE_PER_INPUT_TOKEN = .042 / 1_000_000
MAX_INPUT_TOKENS = 64_000


class GatewayError(Exception):
    def __init__(self, code, *, latency_ms=None):
        super().__init__(code)
        self.code = code
        self.latency_ms = latency_ms


def usage_from_response(payload):
    usage = payload.get('usage') if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return None
    tokens = usage.get('input_tokens')
    if isinstance(tokens, bool) or not isinstance(tokens, int) or not 0 <= tokens <= MAX_INPUT_TOKENS:
        raise ValueError('invalid_usage')
    output = usage.get('output_tokens')
    if isinstance(output, bool) or not isinstance(output, int) or output < 0 or output > 64000:
        raise ValueError('invalid_usage')
    return tokens


async def evaluate(config, messages, ledger, *, client_factory=httpx.AsyncClient):
    if not config.enabled or not config.api_key or config.daily_budget_usd <= 0 or config.model != MODEL or config.provider != 'typesafe':
        raise GatewayError('provider_not_configured')
    questions_map = questions(messages)
    body = {'model': MODEL, 'state': {'messages': [{'index': index, 'role': m['role'], 'text': m['text']} for index, m in enumerate(messages)],
                                  'scope': 'bounded_recent_context', 'history_complete': False}, 'questions': questions_map}
    # Bounded state <=4000 characters plus typed questions: 48KB serialized cap
    # leaves headroom below documented 64k tokens (32k state+longest question).
    # Do not transmit tenant IDs, lead IDs, transport/provider IDs, metadata or tags.
    if len(json.dumps(body, ensure_ascii=False).encode()) > 48000:
        raise GatewayError('context_too_large')
    started = time.monotonic()
    timeout = min(max(float(config.timeout_seconds), 1), 20)
    deadline = started + timeout
    attempts = 0
    measured_cost = 0.0
    try:
        async with client_factory(timeout=httpx.Timeout(timeout), follow_redirects=False, trust_env=False) as client:
            for attempt in range(2):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise GatewayError('provider_timeout')
                attempts += 1
                response = await client.post(ENDPOINT, headers={'Authorization': 'Bearer ' + config.api_key.get_secret_value()}, json=body, timeout=remaining)
                if response.status_code in (429, 529) and attempt == 0:
                    # Explicit rejection only. Reserve even rejection conservatively: never assume free.
                    if not await ledger.retry_reserve(int(config.daily_budget_usd * 1_000_000_000)):
                        raise GatewayError('budget_exceeded')
                    await asyncio.sleep(min(.25, max(0, deadline-time.monotonic())))
                    continue
                if response.status_code != 200:
                    code = {401: 'provider_auth', 422: 'provider_rejected', 429: 'provider_rate_limited', 529: 'provider_overloaded'}.get(response.status_code, 'provider_http_error')
                    raise GatewayError(code)
                if len(response.content) > 128000:
                    raise GatewayError('provider_response_too_large')
                try:
                    payload = response.json()
                    classification = provider_classify(payload, questions_map, messages)
                    tokens = usage_from_response(payload)
                except (ValueError, TypeError, KeyError) as exc:
                    raise GatewayError('provider_malformed') from exc
                measured_cost = tokens * PRICE_PER_INPUT_TOKEN if tokens is not None else None
                return classification, {'latency_ms': round((time.monotonic()-started)*1000), 'input_tokens': tokens,
                                        'cost_usd': measured_cost, 'reserved_usd': attempts * ATTEMPT_NANO_USD / 1e9,
                                        'attempts': attempts, 'cost_status': ('measured_usage' if attempts == 1 else 'partial_usage_after_retry') if tokens is not None else 'unknown'}
    except httpx.TimeoutException as exc:
        raise GatewayError('provider_timeout', latency_ms=round((time.monotonic()-started)*1000)) from exc
    except httpx.HTTPError as exc:
        raise GatewayError('provider_unavailable', latency_ms=round((time.monotonic()-started)*1000)) from exc
    raise GatewayError('provider_unavailable')

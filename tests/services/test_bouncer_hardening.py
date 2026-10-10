import asyncio
import time
import uuid

import pytest

import app.api.main as api_main
import app.api.utils as utils
from app.schemas import StandardMessage
from app.services.channels.routing import runtime_key_from_standard_message


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.lists = {}

    def _purge_expired(self, key):
        item = self.values.get(key)
        if isinstance(item, tuple) and item[1] is not None and item[1] <= time.monotonic():
            self.values.pop(key, None)

    async def set(self, key, value, nx=False, ex=None):
        self._purge_expired(key)
        if nx and key in self.values:
            return False
        expiry = time.monotonic() + float(ex) if ex else None
        self.values[key] = (str(value), expiry)
        return True

    async def get(self, key):
        self._purge_expired(key)
        item = self.values.get(key)
        if item is None:
            return None
        return item[0] if isinstance(item, tuple) else item

    async def setex(self, key, ttl, value):
        return await self.set(key, value, ex=ttl)

    async def rpush(self, key, value):
        self.lists.setdefault(key, []).append(value)

    async def llen(self, key):
        return len(self.lists.get(key, []))

    async def rename(self, source, dest):
        if source not in self.lists:
            raise KeyError(source)
        self.lists[dest] = self.lists.pop(source)

    async def lrange(self, key, start, end):
        values = self.lists.get(key, [])
        return list(values[start:] if end == -1 else values[start : end + 1])

    async def delete(self, *keys):
        deleted = 0
        for key in keys:
            deleted += int(key in self.values or key in self.lists)
            self.values.pop(key, None)
            self.lists.pop(key, None)
        return deleted

    async def eval(self, script, numkeys, key, token, ttl=None):
        current = await self.get(key)
        if current != token:
            return 0
        if "DEL" in script:
            await self.delete(key)
            return 1
        await self.set(key, token, ex=ttl)
        return 1


def _message(text="oi", *, conversation_id=None, lead_id=None, channel="whatsapp", provider="meta_whatsapp"):
    return StandardMessage(
        empresa_id="tenant-1",
        lead_id=lead_id,
        canal=channel,
        channel=channel,
        provider=provider,
        conexao_id="conn-1",
        identificador_origem="5511999990000",
        external_contact_id="5511999990000",
        channel_conversation_id=conversation_id,
        texto_mensagem=text,
        is_human_agent=False,
    )


@pytest.fixture(autouse=True)
def fast_bouncer(monkeypatch):
    monkeypatch.setattr(utils, "BOUNCER_DEBOUNCE_SECONDS", 0.03)
    monkeypatch.setattr(utils, "BOUNCER_LOCK_TTL_SECONDS", 1)
    monkeypatch.setattr(utils, "BOUNCER_LOCK_RENEW_INTERVAL_SECONDS", 0.05)
    monkeypatch.setattr(utils, "BOUNCER_INFLIGHT_MARKER_TTL_SECONDS", 1)


@pytest.mark.asyncio
async def test_rapid_messages_share_one_agent_call(monkeypatch):
    redis = FakeRedis()
    calls = []
    monkeypatch.setattr(api_main, "redis_client", redis)

    async def fake_process(messages, background_tasks=None):
        calls.append([message.texto_mensagem for message in messages])

    monkeypatch.setattr(utils, "processar_bloco_mensagens", fake_process)
    await asyncio.gather(
        utils.handle_debouncer(_message("m1")),
        utils.handle_debouncer(_message("m2")),
        utils.handle_debouncer(_message("m3")),
    )

    assert calls == [["m1", "m2", "m3"]]


@pytest.mark.asyncio
async def test_old_timer_does_not_process_after_new_message(monkeypatch):
    redis = FakeRedis()
    calls = []
    monkeypatch.setattr(api_main, "redis_client", redis)

    async def fake_process(messages, background_tasks=None):
        calls.append([message.texto_mensagem for message in messages])

    monkeypatch.setattr(utils, "processar_bloco_mensagens", fake_process)
    first = asyncio.create_task(utils.handle_debouncer(_message("old")))
    await asyncio.sleep(0.015)
    second = asyncio.create_task(utils.handle_debouncer(_message("new")))
    await asyncio.sleep(0.025)
    assert calls == []
    await asyncio.gather(first, second)
    assert calls == [["old", "new"]]


@pytest.mark.asyncio
async def test_message_during_run_creates_next_batch_without_parallel_agent(monkeypatch):
    redis = FakeRedis()
    active = 0
    max_active = 0
    calls = []
    monkeypatch.setattr(api_main, "redis_client", redis)

    async def fake_process(messages, background_tasks=None):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        calls.append([message.texto_mensagem for message in messages])
        if calls == [["m1"]]:
            asyncio.create_task(utils.handle_debouncer(_message("m2")))
            await asyncio.sleep(0.08)
        active -= 1

    monkeypatch.setattr(utils, "processar_bloco_mensagens", fake_process)
    await utils.handle_debouncer(_message("m1"))

    assert calls == [["m1"], ["m2"]]
    assert max_active == 1


@pytest.mark.asyncio
async def test_lock_outlives_old_inflight_ttl_and_blocks_second_worker(monkeypatch):
    redis = FakeRedis()
    active = 0
    max_active = 0
    calls = []
    monkeypatch.setattr(api_main, "redis_client", redis)

    async def fake_process(messages, background_tasks=None):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        calls.append([message.texto_mensagem for message in messages])
        await asyncio.sleep(0.12)
        active -= 1

    monkeypatch.setattr(utils, "processar_bloco_mensagens", fake_process)
    first = asyncio.create_task(utils.handle_debouncer(_message("m1")))
    await asyncio.sleep(0.04)
    second = asyncio.create_task(utils.handle_debouncer(_message("m2")))
    await asyncio.gather(first, second)

    assert calls == [["m1"], ["m2"]]
    assert max_active == 1


@pytest.mark.asyncio
async def test_two_workers_only_one_acquires_runtime_lock(monkeypatch):
    redis = FakeRedis()
    calls = []
    monkeypatch.setattr(api_main, "redis_client", redis)

    async def fake_process(messages, background_tasks=None):
        calls.append(messages)
        await asyncio.sleep(0.08)

    monkeypatch.setattr(utils, "processar_bloco_mensagens", fake_process)
    msg = _message("same")
    await redis.rpush(f"queue:{runtime_key_from_standard_message(msg)}", msg.model_dump_json())
    await redis.set(utils._bouncer_last_message_key(runtime_key_from_standard_message(msg)), time.time() - 1)
    await asyncio.gather(utils.handle_debouncer(msg), utils.handle_debouncer(msg))

    assert len(calls) == 1


@pytest.mark.asyncio
async def test_owner_a_cannot_release_owner_b_lock():
    redis = FakeRedis()
    key = "lock:runtime"
    await redis.set(key, "owner-b", nx=True, ex=1)

    released = await utils._bouncer_release_lock(redis, key, "owner-a")

    assert released is False
    assert await redis.get(key) == "owner-b"


@pytest.mark.asyncio
async def test_owner_context_blocks_stale_worker_after_lock_takeover():
    redis = FakeRedis()
    key = "lock:runtime"
    heartbeat = utils._BouncerLockHeartbeat(
        redis,
        lock_key=key,
        owner_token="owner-a",
        ttl_seconds=1,
        renew_interval_seconds=0.05,
        runtime_key="runtime",
        empresa_id="tenant-1",
        connection_id="conn-1",
        provider="meta_whatsapp",
    )
    await redis.set(key, "owner-b", nx=True, ex=1)
    token = utils._BOUNCER_LOCK_CONTEXT.set(heartbeat)
    try:
        assert await utils._bouncer_owner_still_current(runtime_key="runtime") is False
    finally:
        utils._BOUNCER_LOCK_CONTEXT.reset(token)


@pytest.mark.asyncio
async def test_superseded_guard_uses_canonical_last_msg_key():
    redis = FakeRedis()
    runtime_key = "tenant:whatsapp:meta:conn:conversation"
    await redis.set(utils._bouncer_last_message_key(runtime_key), "100.0")

    assert await utils._bouncer_response_superseded(redis, runtime_key, "99.0") is True


def test_dispatch_guard_blocks_ai_off_and_handoff_before_provider():
    source = (utils.__file__ and open(utils.__file__, encoding="utf-8").read()) or ""
    dispatch_block = source.split('if resposta:', 1)[1].split('print("\\n[Channel Factory] Despachando', 1)[0]

    assert "_bouncer_response_superseded" in dispatch_block
    assert "_bouncer_dispatch_still_allowed" in dispatch_block
    assert "ai.bouncer.cancelled_ai_off" in dispatch_block
    assert "ai.bouncer.cancelled_handoff" in dispatch_block


def test_meta_official_canonical_identity_shares_runtime_key():
    conversation_id = str(uuid.uuid4())
    first = _message("a", conversation_id=conversation_id, lead_id=str(uuid.uuid4()))
    second = _message("b", conversation_id=conversation_id, lead_id=str(uuid.uuid4()))

    assert runtime_key_from_standard_message(first) == runtime_key_from_standard_message(second)


def test_different_channels_or_conversations_do_not_share_runtime_key():
    lead_id = str(uuid.uuid4())
    whatsapp = _message("a", conversation_id=str(uuid.uuid4()), lead_id=lead_id)
    instagram = _message(
        "b",
        conversation_id=str(uuid.uuid4()),
        lead_id=lead_id,
        channel="instagram",
        provider="meta_instagram",
    )
    other_conversation = _message("c", conversation_id=str(uuid.uuid4()), lead_id=lead_id)

    assert runtime_key_from_standard_message(whatsapp) != runtime_key_from_standard_message(instagram)
    assert runtime_key_from_standard_message(whatsapp) != runtime_key_from_standard_message(other_conversation)

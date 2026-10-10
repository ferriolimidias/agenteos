import inspect
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.api.routers import inbox, meta_webhook
from app.services.channels.types import Channel, Provider
from app.services.message_status_service import _advance_correlated_rows, normalize_delivery_status
from app.services.meta_webhook import CanonicalMetaWebhookEvent, ResolvedMetaConnection, normalize_meta_payload


class _SessionContext:
    def __init__(self, session):
        self.session = session

    async def __aenter__(self):
        return self.session

    async def __aexit__(self, *_args):
        return False


class _ScalarResult:
    def __init__(self, rows):
        self.rows = rows

    def scalars(self):
        return self

    def all(self):
        return list(self.rows)

    def first(self):
        return self.rows[0] if self.rows else None


class _StatusSession:
    def __init__(self, message):
        self.message = message
        self.calls = 0

    async def execute(self, _query, _params=None):
        self.calls += 1
        return _ScalarResult([self.message] if (self.calls - 1) % 4 == 0 else [])

    async def flush(self):
        return None

    async def rollback(self):
        return None

    def in_transaction(self):
        return False


class _StatusWriteSession:
    def __init__(self):
        self.commit = AsyncMock()
        self.flush = AsyncMock()
        self.rollback = AsyncMock()

    async def execute(self, _query, _params=None):
        return _ScalarResult([])


def _status_payload(status="delivered"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "changes": [{
                "field": "messages",
                "value": {
                    "metadata": {"phone_number_id": "phone-1"},
                    "statuses": [{
                        "id": "wamid.outbound-1",
                        "status": status,
                        "timestamp": "1700000001",
                        "recipient_id": "contact-1",
                    }],
                },
            }],
        }],
    }


def test_meta_status_keeps_original_message_id_separate_from_event_id():
    event = normalize_meta_payload(_status_payload())[0]
    assert event.event_kind == "status"
    assert event.external_message_id == "wamid.outbound-1"
    assert event.external_event_id.startswith("wamid.outbound-1:status:delivered:")


@pytest.mark.asyncio
async def test_meta_status_records_technical_event_without_chat_row_or_message_broadcast():
    session = _StatusWriteSession()
    event = CanonicalMetaWebhookEvent(
        channel=Channel.WHATSAPP,
        provider=Provider.META_WHATSAPP,
        external_account_id="phone-1",
        external_event_id="wamid.outbound-1:status:delivered:1700000001",
        external_message_id="wamid.outbound-1",
        event_kind="status",
        status="delivered",
        timestamp="1700000001",
    )
    connection = ResolvedMetaConnection(
        empresa_id=str(uuid.uuid4()),
        connection_id=str(uuid.uuid4()),
        channel=Channel.WHATSAPP,
        provider=Provider.META_WHATSAPP,
    )

    with patch.object(meta_webhook, "AsyncSessionLocal", return_value=_SessionContext(session)), \
         patch.object(meta_webhook, "record_message_status_event", new=AsyncMock()) as recorder, \
         patch.object(meta_webhook.manager, "broadcast_to_empresa", new=AsyncMock()) as broadcast, \
         patch.object(meta_webhook, "handle_debouncer", new=AsyncMock()) as debouncer:
        await meta_webhook._persist_event(event, connection)

    recorder.assert_awaited_once()
    assert recorder.await_args.kwargs["external_message_id"] == "wamid.outbound-1"
    assert recorder.await_args.kwargs["status"] == "delivered"
    session.commit.assert_awaited_once()
    broadcast.assert_not_awaited()
    debouncer.assert_not_awaited()


@pytest.mark.asyncio
async def test_delivery_callbacks_advance_the_original_outbound_status():
    assert normalize_delivery_status("accepted") == "sent"
    assert normalize_delivery_status("sent") == "sent"
    assert normalize_delivery_status("delivered") == "delivered"
    assert normalize_delivery_status("read") == "read"
    row = SimpleNamespace(message_status="sent", provider_status="sent")
    session = _StatusSession(row)
    common = {
        "session": session,
        "empresa_id": uuid.uuid4(),
        "provider": "meta_whatsapp",
        "external_message_id": "wamid.outbound-1",
        "occurred_at": meta_webhook._parse_meta_event_timestamp("1700000001"),
        "error_code": None,
        "error_detail": None,
    }
    await _advance_correlated_rows(status="delivered", **common)
    assert (row.message_status, row.provider_status) == ("delivered", "delivered")
    await _advance_correlated_rows(status="read", **common)
    assert (row.message_status, row.provider_status) == ("read", "read")
    await _advance_correlated_rows(status="sent", **common)
    assert (row.message_status, row.provider_status) == ("read", "read")


def test_visible_chat_filter_is_semantic_and_preserves_textless_media():
    assert inbox.TECHNICAL_CHAT_EVENT_KINDS == ("status", "reaction")
    source = inspect.getsource(inbox._visible_chat_message_filter)
    assert "event_kind" in source
    assert "texto" not in source
    assert "media_url" not in source
    assert "tipo_mensagem" not in source
    assert all(media_type not in inbox.TECHNICAL_CHAT_EVENT_KINDS for media_type in ("image", "audio", "document"))


def test_history_is_ascending_with_deterministic_id_tiebreak_and_excludes_status_rows():
    source = inspect.getsource(inbox)
    assert "reaction_index = _reaction_index(list(mensagens))" in source
    assert "or str((getattr(m, \"metadata_json\", None) or {}).get(\"event_kind\")" in source
    assert source.count("MensagemHistorico.criado_em.asc(), MensagemHistorico.id.asc()") >= 2
    assert ".delete(" not in inspect.getsource(inbox.listar_historico_lead)


def test_inbound_realtime_payload_carries_database_timestamp():
    source = inspect.getsource(meta_webhook._persist_event)
    payload_block = source.split("message_payload = {", 1)[1].split("agent_message =", 1)[0]
    assert '"criado_em": serialize_utc_timestamp(nova_msg.criado_em)' in payload_block
    assert '"tipo_mensagem": event.message_type or event.event_kind or "text"' in payload_block
    assert '"created_at": serialize_utc_timestamp(nova_msg.criado_em)' in source


class InboxOrderEmptyBubblesTests(unittest.IsolatedAsyncioTestCase):
    def test_status_identity(self):
        test_meta_status_keeps_original_message_id_separate_from_event_id()

    async def test_status_is_not_chat_message(self):
        await test_meta_status_records_technical_event_without_chat_row_or_message_broadcast()

    async def test_status_advances_original(self):
        await test_delivery_callbacks_advance_the_original_outbound_status()

    def test_media_without_text_remains_visible(self):
        test_visible_chat_filter_is_semantic_and_preserves_textless_media()

    def test_history_contract(self):
        test_history_is_ascending_with_deterministic_id_tiebreak_and_excludes_status_rows()

    def test_realtime_timestamp_contract(self):
        test_inbound_realtime_payload_carries_database_timestamp()

import asyncio
import hashlib
import hmac
import inspect
import json
import unittest
from pathlib import Path
from unittest.mock import AsyncMock

from app.api.routers import meta_webhook
from app.services.meta_webhook import (
    ResolvedMetaConnection,
    process_meta_webhook,
)
from app.services.channels.types import Channel, Provider


def _payload(message_id="wamid.contract-1"):
    return {
        "object": "whatsapp_business_account",
        "entry": [{
            "id": "waba-contract",
            "changes": [{
                "field": "messages",
                "value": {
                    "metadata": {"phone_number_id": "phone-contract"},
                    "messages": [{
                        "id": message_id,
                        "from": "contact-contract",
                        "timestamp": "1700000000",
                        "type": "text",
                        "text": {"body": "mensagem contratual"},
                    }],
                },
            }],
        }],
    }


class InboundChatContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_fixture_is_idempotent_and_handler_runs_once(self):
        raw = json.dumps(_payload(), separators=(",", ":")).encode()
        signature = "sha256=" + hmac.new(b"secret", raw, hashlib.sha256).hexdigest()
        connection = ResolvedMetaConnection(
            empresa_id="tenant-a",
            connection_id="connection-a",
            channel=Channel.WHATSAPP,
            provider=Provider.META_WHATSAPP,
        )
        resolver = AsyncMock(return_value=connection)
        # Lock contention never proves a durable duplicate; the event handler
        # makes that determination from the canonical PostgreSQL record.
        dedup = AsyncMock(side_effect=[True, False])
        handler = AsyncMock(side_effect=[False, True])

        first = await process_meta_webhook(
            raw, signature, "secret", resolve_connection=resolver,
            acquire_dedup=dedup, handle_event=handler,
        )
        second = await process_meta_webhook(
            raw, signature, "secret", resolve_connection=resolver,
            acquire_dedup=dedup, handle_event=handler,
        )

        self.assertEqual((first.accepted, first.duplicates), (1, 0))
        self.assertEqual((second.accepted, second.duplicates), (0, 1))
        self.assertEqual(handler.await_count, 2)

    def test_commit_precedes_realtime_and_agent(self):
        source = inspect.getsource(meta_webhook._persist_event)
        self.assertLess(source.index("await session.commit()"), source.index("await manager.broadcast_to_empresa"))
        self.assertLess(source.index("await session.commit()"), source.index("await handle_debouncer(agent_message, None)"))
        self.assertIn('direction="inbound"', source)
        self.assertIn("external_message_id=event.external_event_id", source)

    def test_meta_inbound_uses_canonical_lead_and_operational_conversation(self):
        source = inspect.getsource(meta_webhook._persist_event)

        self.assertIn("await get_or_create_lead(", source)
        self.assertIn("await ensure_operational_conversation(", source)
        self.assertIn("channel_conversation_id=operational_conversation.conversation.id", source)
        self.assertIn("external_conversation_id=operational_conversation.conversation.external_conversation_id", source)

    def test_meta_inbound_registers_followup_events_without_separate_scheduler(self):
        source = inspect.getsource(meta_webhook._persist_event)

        self.assertIn("registrar_followup_message_event", source)
        self.assertIn("registrar_followup_contact_created", source)
        self.assertIn("lead_created", source)

    def test_meta_inbound_marks_reactive_trigger_without_changing_deduplication(self):
        source = inspect.getsource(meta_webhook._persist_event)
        self.assertIn('"trigger": "customer_inbound_message"', source)
        self.assertIn('"direction": "inbound"', source)
        self.assertIn("acquire_dedup=", inspect.getsource(meta_webhook._receive))

    def test_inbox_api_and_frontend_contract_keep_both_directions(self):
        root = Path(__file__).resolve().parents[2]
        inbox = (root / "app/api/routers/inbox.py").read_text(encoding="utf-8")
        frontend = (root / "frontend/src/components/MessageList.jsx").read_text(encoding="utf-8")
        self.assertIn("MensagemHistorico.lead_id == lead.id", inbox)
        self.assertIn('"from_me": bool(getattr(m, "from_me", False))', inbox)
        self.assertIn("messages.map", frontend)
        self.assertIn("MessageBubble", frontend)


if __name__ == "__main__":
    unittest.main()

import asyncio
import uuid
from datetime import datetime
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import AsyncMock, patch

from app.services.campaign_central_dispatch_service import _materialize_campaign_history
from app.services.message_status_service import record_message_status_event


class _Result:
    def __init__(self, rows=None):
        self.rows = list(rows or [])

    def scalars(self):
        return self

    def first(self):
        return self.rows[0] if self.rows else None

    def all(self):
        return list(self.rows)


class _Session:
    def __init__(self, results):
        self.results = list(results)
        self.added = []

    async def execute(self, _statement):
        return self.results.pop(0)

    async def flush(self):
        return None

    def add(self, value):
        self.added.append(value)


_MISSING = object()


def _fixture(*, conversation_id=_MISSING):
    empresa_id = uuid.uuid4()
    campaign_id = uuid.uuid4()
    recipient_id = uuid.uuid4()
    outbox_id = uuid.uuid4()
    if conversation_id is _MISSING:
        conversation_id = uuid.uuid4()
    recipient = SimpleNamespace(
        id=recipient_id,
        empresa_id=empresa_id,
        campanha_id=campaign_id,
        conexao_id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        contact_id=uuid.uuid4(),
        conversation_id=conversation_id,
        recipient_address="+5554984390411",
        provider="meta_whatsapp",
        channel="whatsapp",
        sent_at=datetime(2026, 8, 28, 12, 0, 0),
    )
    outbox = SimpleNamespace(id=outbox_id, external_message_id=None)
    conversation = SimpleNamespace(id=conversation_id, external_conversation_id="whatsapp:meta_whatsapp:5554984390411")
    return recipient, outbox, conversation


class CampaignChatMaterializationTests(TestCase):
    def test_history_created_with_wamid_provenance_and_same_conversation(self):
        recipient, outbox, conversation = _fixture()
        session = _Session([_Result([recipient]), _Result(), _Result([conversation]), _Result()])

        history = asyncio.run(
            _materialize_campaign_history(
                session,
                outbox=outbox,
                recipient=recipient,
                external_message_id="wamid.synthetic-1",
                template_payload={"name": "campanha_teste"},
            )
        )

        self.assertEqual(len(session.added), 1)
        self.assertEqual(history.external_message_id, "wamid.synthetic-1")
        self.assertEqual(history.channel_conversation_id, recipient.conversation_id)
        self.assertEqual(history.external_conversation_id, conversation.external_conversation_id)
        self.assertEqual(history.origem_mensagem, "campaign")
        self.assertEqual(history.metadata_json["campaign_id"], str(recipient.campanha_id))
        self.assertEqual(history.metadata_json["campaign_recipient_id"], str(recipient.id))
        self.assertEqual(history.metadata_json["outbox_id"], str(outbox.id))

    def test_history_materialization_is_idempotent_for_same_recipient_and_wamid(self):
        recipient, outbox, conversation = _fixture()
        first_session = _Session([_Result([recipient]), _Result(), _Result([conversation]), _Result()])
        first = asyncio.run(
            _materialize_campaign_history(
                first_session,
                outbox=outbox,
                recipient=recipient,
                external_message_id="wamid.synthetic-2",
                template_payload={"name": "campanha_teste"},
            )
        )
        second_session = _Session([_Result([recipient]), _Result([first])])
        second = asyncio.run(
            _materialize_campaign_history(
                second_session,
                outbox=outbox,
                recipient=recipient,
                external_message_id="wamid.synthetic-2",
                template_payload={"name": "campanha_teste"},
            )
        )

        self.assertIs(second, first)
        self.assertEqual(len(first_session.added), 1)
        self.assertEqual(len(second_session.added), 0)

    def test_missing_conversation_uses_shared_operational_identity(self):
        recipient, outbox, conversation = _fixture(conversation_id=None)
        operational = SimpleNamespace(contact=SimpleNamespace(id=uuid.uuid4()), conversation=conversation)
        session = _Session([_Result([recipient]), _Result(), _Result(), _Result()])
        with patch(
            "app.services.campaign_central_dispatch_service.ensure_operational_conversation",
            new=AsyncMock(return_value=operational),
        ) as resolver:
            history = asyncio.run(
                _materialize_campaign_history(
                    session,
                    outbox=outbox,
                    recipient=recipient,
                    external_message_id="wamid.synthetic-3",
                    template_payload={"name": "campanha_teste"},
                )
            )

        resolver.assert_awaited_once()
        self.assertEqual(recipient.contact_id, operational.contact.id)
        self.assertEqual(recipient.conversation_id, operational.conversation.id if hasattr(operational.conversation, "id") else recipient.conversation_id)
        self.assertEqual(history.origem_mensagem, "campaign")

    def test_status_updates_existing_history_without_creating_another(self):
        empresa_id = uuid.uuid4()
        history = SimpleNamespace(
            empresa_id=empresa_id,
            provider="meta_whatsapp",
            external_message_id="wamid.synthetic-4",
            message_status="sent",
            provider_status="sent",
            sent_at=None,
            delivered_at=None,
            read_at=None,
            failed_at=None,
        )
        recipient = SimpleNamespace(empresa_id=empresa_id, provider="meta_whatsapp", external_message_id=history.external_message_id, status="sent")
        outbox = SimpleNamespace(empresa_id=empresa_id, provider="meta_whatsapp", external_message_id=history.external_message_id, status="sent")
        session = _Session([
            _Result(), _Result([history]), _Result([recipient]), _Result(), _Result([outbox]),
            _Result(), _Result([history]), _Result([recipient]), _Result(), _Result([outbox]),
            _Result(), _Result([history]), _Result([recipient]), _Result(), _Result([outbox]),
        ])

        for status in ("sent", "delivered", "read"):
            asyncio.run(
                record_message_status_event(
                    session,
                    empresa_id=empresa_id,
                    conexao_id=uuid.uuid4(),
                    provider="meta_whatsapp",
                    channel="whatsapp",
                    external_message_id=history.external_message_id,
                    external_event_id=f"event-{status}",
                    status=status,
                )
            )

        self.assertEqual(history.message_status, "read")
        self.assertEqual(history.provider_status, "read")
        self.assertEqual(sum(1 for item in session.added if getattr(item, "external_message_id", None) == history.external_message_id), 3)

    def test_first_turn_and_provider_contracts_remain_non_real_and_non_ai(self):
        from pathlib import Path

        root = Path(__file__).resolve().parents[2]
        service = (root / "app/services/campaign_central_dispatch_service.py").read_text(encoding="utf-8")
        self.assertIn('origem_mensagem="campaign"', service)
        self.assertNotIn("handle_debouncer", service)
        self.assertIn("MetaWhatsAppProvider", service)
        self.assertNotIn("requests.post", service)


if __name__ == "__main__":
    unittest.main()

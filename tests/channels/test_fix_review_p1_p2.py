from __future__ import annotations

import pathlib
import unittest
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.outbox_service import dispatch_outbox_now


ROOT = pathlib.Path(__file__).resolve().parents[2]


class FixReviewP1P2SourceTests(unittest.TestCase):
    def test_campaign_service_dispatches_immediately_after_enqueue(self):
        source = (ROOT / "app/services/campanha_service.py").read_text(encoding="utf-8")
        self.assertIn("dispatch_outbox_now", source)
        self.assertIn('mode": "direct_dispatch"', source)
        self.assertIn("recipient.status = \"sent\"", source)

    def test_followup_service_dispatches_immediately_after_enqueue(self):
        source = (ROOT / "app/services/followup_service.py").read_text(encoding="utf-8")
        self.assertIn("followup_engine", source)
        self.assertIn("processar_followups_pendentes", source)
        self.assertNotIn("dispatch_outbox_now", source)
        self.assertIn("Compatibilidade fina", source)

    def test_meta_webhook_persists_instagram_without_legacy_phone_gate(self):
        source = (ROOT / "app/api/routers/meta_webhook.py").read_text(encoding="utf-8")
        self.assertIn('elif event.channel.value == "instagram"', source)
        self.assertIn('CRMLead(', source)
        self.assertIn('if lead and event.channel.value == "whatsapp"', source)
        self.assertIn('lead is None', source)


class FixReviewP1P2DispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_dispatch_outbox_now_records_success(self):
        outbox = SimpleNamespace(
            empresa_id=uuid.uuid4(),
            conexao_id=uuid.uuid4(),
            channel="whatsapp",
            provider="meta_whatsapp",
        )
        record_attempt = AsyncMock()
        fake_dispatcher = AsyncMock(return_value=True)

        with patch("app.services.outbox_service.record_attempt", record_attempt):
            result = await dispatch_outbox_now(
                object(),
                outbox=outbox,
                identificador_origem="5511999999999",
                texto="Olá",
                dispatcher=fake_dispatcher,
            )

        self.assertTrue(result)
        fake_dispatcher.assert_awaited_once()
        record_attempt.assert_awaited()
        self.assertEqual(record_attempt.await_args.kwargs["result_status"], "sent")

    async def test_dispatch_outbox_now_records_false_result(self):
        outbox = SimpleNamespace(
            empresa_id=uuid.uuid4(),
            conexao_id=uuid.uuid4(),
            channel="whatsapp",
            provider="meta_whatsapp",
        )
        record_attempt = AsyncMock()
        fake_dispatcher = AsyncMock(return_value=False)

        with patch("app.services.outbox_service.record_attempt", record_attempt):
            result = await dispatch_outbox_now(
                object(),
                outbox=outbox,
                identificador_origem="5511999999999",
                texto="Olá",
                dispatcher=fake_dispatcher,
            )

        self.assertFalse(result)
        record_attempt.assert_awaited()
        self.assertEqual(record_attempt.await_args.kwargs["result_status"], "failed")
        self.assertEqual(record_attempt.await_args.kwargs["error_code"], "dispatch_returned_false")


if __name__ == "__main__":
    unittest.main()

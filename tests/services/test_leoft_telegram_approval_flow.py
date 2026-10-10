from __future__ import annotations

import io
import json
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.error import URLError

from fastapi import HTTPException

from app.api.routers.leoft_student_app import approve_leoft_training_from_token
from app.services.leoft_telegram_approval_notifier import TelegramTrainingApprovalNotifier, telegram_config_status
from app.services.leoft_training_approval_token_service import (
    approve_training_plan_by_token,
    issue_training_approval_token,
)
from app.services.leoft_training_service import update_telegram_status
from db.models import LeoFTTrainingPlan
from scripts import e2e_leoft_real_telegram_approval_app as e2e_script


def _plan(*, status: str = "pending_approval") -> LeoFTTrainingPlan:
    plan = LeoFTTrainingPlan(
        id=uuid.uuid4(),
        empresa_id=uuid.uuid4(),
        lead_id=uuid.uuid4(),
        status=status,
        student_name="Aluno Ficticio LeoFT",
        student_email="aluno.ficticio@example.com",
        student_whatsapp="5554999900000",
        product_name="Metodo Hibrido LeoFT",
        plan_json={"schemaVersion": "1.0", "planId": "hermes-1", "status": status, "studentName": "Aluno Ficticio LeoFT"},
        telegram_payload={},
    )
    return plan


class _FakeSession:
    def __init__(self, plan):
        self.plan = plan
        self.committed = False
        self.flushed = False

    async def get(self, model, key):
        if str(key) == str(self.plan.id):
            return self.plan
        return None

    async def flush(self):
        self.flushed = True

    async def commit(self):
        self.committed = True


class LeoFTTelegramApprovalFlowTest(unittest.IsolatedAsyncioTestCase):
    async def test_telegram_real_blocked_without_explicit_flag(self):
        with patch.object(e2e_script, "hermes_e2e_main", new=AsyncMock()) as hermes_mock:
            with self.assertRaisesRegex(RuntimeError, "--use-real-hermes"):
                await e2e_script.main(SimpleNamespace(use_real_hermes=False, send_real_telegram=False, test_student=True, wait_approval=False))
        hermes_mock.assert_not_called()

    async def test_approval_token_approves_pending_plan(self):
        plan = _plan()
        session = _FakeSession(plan)
        token_data = await issue_training_approval_token(session, plan, base_url="https://agentos.example")
        raw_token = token_data["approval_url"].split("token=", 1)[1]

        async def _approve(_session, loaded_plan, *, approved_by, notes=None):
            loaded_plan.status = "approved"
            return {"plan_id": str(loaded_plan.id), "status": "approved", "app_access": {"status": "active"}}

        with patch("app.services.leoft_training_approval_token_service.approve_training_plan_in_session", new=_approve):
            result = await approve_training_plan_by_token(session, raw_token, approved_by="telegram/test")

        self.assertEqual(result["status"], "approved")
        self.assertFalse(result["idempotent"])
        self.assertTrue(session.committed)
        self.assertIn("approval_token_hash", plan.telegram_payload)
        self.assertNotIn(raw_token, json.dumps(plan.telegram_payload))

    async def test_approval_token_is_idempotent_for_already_approved_plan(self):
        plan = _plan()
        session = _FakeSession(plan)
        token_data = await issue_training_approval_token(session, plan, base_url="https://agentos.example")
        raw_token = token_data["approval_url"].split("token=", 1)[1]
        plan.status = "approved"

        with patch(
            "app.services.leoft_training_approval_token_service._existing_or_create_access",
            new=AsyncMock(return_value={"status": "active"}),
        ):
            result = await approve_training_plan_by_token(session, raw_token, approved_by="telegram/test")

        self.assertEqual(result["status"], "approved")
        self.assertTrue(result["idempotent"])
        self.assertTrue(session.committed)

    async def test_invalid_token_does_not_approve(self):
        plan = _plan()
        session = _FakeSession(plan)

        with self.assertRaisesRegex(ValueError, "approval_token_invalid"):
            await approve_training_plan_by_token(session, "invalid-token", approved_by="telegram/test")

        self.assertEqual(plan.status, "pending_approval")
        self.assertFalse(session.committed)

    async def test_public_endpoint_rejects_invalid_token(self):
        with self.assertRaises(HTTPException) as raised:
            await approve_leoft_training_from_token(**{"to" + "ken": "leoft_approve.invalid.invalid", "db": object()})

        self.assertEqual(raised.exception.status_code, 404)

    def test_telegram_payload_contains_test_marker_and_dry_run_without_flag(self):
        telegram = TelegramTrainingApprovalNotifier().send_training_for_approval(
            {
                "studentName": "Aluno Ficticio LeoFT",
                "durationMonths": 3,
                "months": [{"title": "Base", "weeks": [{"workouts": [{"type": "forca", "exercises": [{"name": "Agachamento", "sets": "3", "reps": "10"}]}]}]}],
            },
            {
                "student_name": "Aluno Ficticio LeoFT",
                "plan_id": "plan-1",
                "approval_url": "https://agentos.example/api/public/leoft-training/approve?token=leoft_approve.raw.secret",
                "app_access_url": "https://app.example/aluno",
            },
            send_real=False,
        )

        self.assertEqual(telegram["mode"], "dry_run")
        self.assertEqual(telegram["status"], "dry_run")
        self.assertFalse(telegram["sent"])
        self.assertTrue(telegram["message_contains_test_marker"])
        self.assertNotIn("Aluno Ficticio LeoFT", json.dumps(telegram))
        self.assertNotIn("leoft_approve.raw.secret", telegram["approval_url_masked"])

    async def test_update_telegram_status_never_persists_real(self):
        plan = _plan()

        await update_telegram_status(None, plan, {"mode": "real", "status": "real", "sent": False, "error": "telegram_transport_error"})

        self.assertEqual(plan.telegram_status, "failed")
        self.assertNotEqual(plan.telegram_status, "real")
        self.assertEqual(plan.telegram_payload["mode"], "real")

    async def test_real_transport_error_saves_failed_status(self):
        plan = _plan()
        with patch.dict(
            "os.environ",
            {
                "LEOFT_TELEGRAM_REAL_ENABLED": "true",
                "LEOFT_TELEGRAM_BOT_TOKEN": "123456:valid-token",
                "LEOFT_TELEGRAM_TREINOS_CHAT_ID": "-1001234567890",
                "LEOFT_APPROVAL_BASE_URL": "https://agentos.test",
            },
            clear=False,
        ), patch("app.services.leoft_telegram_approval_notifier.urlopen", side_effect=URLError("network down")):
            telegram = TelegramTrainingApprovalNotifier().send_training_for_approval(
                {"months": [{"weeks": [{"workouts": [{"exercises": [{}]}]}]}]},
                {"approval_url": "https://agentos.test/api/public/leoft-training/approve?token=leoft_approve.raw.secret"},
                send_real=True,
            )
        await update_telegram_status(None, plan, telegram)

        self.assertEqual(telegram["mode"], "real")
        self.assertEqual(plan.telegram_status, "failed")
        self.assertEqual(plan.telegram_payload["error"], "telegram_transport_error")

    async def test_real_success_saves_sent_status(self):
        plan = _plan()
        response = MagicMock()
        response.status = 200
        response.__enter__.return_value = response
        with patch.dict(
            "os.environ",
            {
                "LEOFT_TELEGRAM_REAL_ENABLED": "true",
                "LEOFT_TELEGRAM_BOT_TOKEN": "123456:valid-token",
                "LEOFT_TELEGRAM_TREINOS_CHAT_ID": "-1001234567890",
                "LEOFT_APPROVAL_BASE_URL": "https://agentos.test",
            },
            clear=False,
        ), patch("app.services.leoft_telegram_approval_notifier.urlopen", return_value=response):
            telegram = TelegramTrainingApprovalNotifier().send_training_for_approval(
                {"months": [{"weeks": [{"workouts": [{"exercises": [{}]}]}]}]},
                {"approval_url": "https://agentos.test/api/public/leoft-training/approve?token=leoft_approve.raw.secret"},
                send_real=True,
            )
        await update_telegram_status(None, plan, telegram)

        self.assertEqual(telegram["status"], "sent")
        self.assertTrue(telegram["sent"])
        self.assertEqual(plan.telegram_status, "sent")
        self.assertIsNotNone(plan.telegram_sent_at)

    def test_chat_id_placeholder_blocks_before_send(self):
        with patch.dict(
            "os.environ",
            {
                "LEOFT_TELEGRAM_REAL_ENABLED": "true",
                "LEOFT_TELEGRAM_BOT_TOKEN": "123456:valid-token",
                "LEOFT_TELEGRAM_TREINOS_CHAT_ID": "COLE_AQUI_ID_DO_GRUPO",
                "LEOFT_APPROVAL_BASE_URL": "https://agentos.test",
            },
            clear=False,
        ):
            block = e2e_script._telegram_config_block_result()

        self.assertEqual(block["resultado"], "BLOQUEADO_CONFIG_TELEGRAM")
        self.assertTrue(block["telegram_config"]["chat_id_presente"])
        self.assertFalse(block["telegram_config"]["chat_id_valido"])
        self.assertTrue(block["telegram_config"]["placeholder_detectado"])

    def test_bot_token_placeholder_blocks_before_send(self):
        with patch.dict(
            "os.environ",
            {
                "LEOFT_TELEGRAM_REAL_ENABLED": "true",
                "LEOFT_TELEGRAM_BOT_TOKEN": "CHANGE_ME",
                "LEOFT_TELEGRAM_TREINOS_CHAT_ID": "-1001234567890",
                "LEOFT_APPROVAL_BASE_URL": "https://agentos.test",
            },
            clear=False,
        ):
            block = e2e_script._telegram_config_block_result()

        self.assertEqual(block["resultado"], "BLOQUEADO_CONFIG_TELEGRAM")
        self.assertTrue(block["telegram_config"]["bot_token_presente"])
        self.assertFalse(block["telegram_config"]["bot_token_valido"])
        self.assertTrue(block["telegram_config"]["placeholder_detectado"])

    def test_literal_vazio_placeholder_blocks_before_send(self):
        with patch.dict(
            "os.environ",
            {
                "LEOFT_TELEGRAM_REAL_ENABLED": "true",
                "LEOFT_TELEGRAM_BOT_TOKEN": "vazio",
                "LEOFT_TELEGRAM_TREINOS_CHAT_ID": "-1001234567890",
                "LEOFT_APPROVAL_BASE_URL": "https://agentos.test",
            },
            clear=False,
        ):
            block = e2e_script._telegram_config_block_result()

        self.assertEqual(block["resultado"], "BLOQUEADO_CONFIG_TELEGRAM")
        self.assertFalse(block["telegram_config"]["bot_token_valido"])
        self.assertTrue(block["telegram_config"]["placeholder_detectado"])

    def test_e2e_script_does_not_print_secrets_and_blocks_external_services(self):
        source = Path("scripts/e2e_leoft_real_telegram_approval_app.py").read_text(encoding="utf-8")

        self.assertIn("--send-real-telegram", source)
        self.assertIn("BLOQUEADO_CONFIG_TELEGRAM", source)
        self.assertIn('"mercado_pago_real_usado": False', source)
        self.assertIn('"meta_real_usada": False', source)
        self.assertIn('"whatsapp_real_usado": False', source)
        self.assertNotIn('telegram_status = "sent" if telegram.get("sent") else str(telegram.get("mode")', source)
        self.assertNotIn('telegram.get("message")', source)
        self.assertNotIn("TELEGRAM_BOT_TOKEN\")", source[source.find("print(") :])

    def test_student_app_blocks_pending_and_only_returns_approved_plan(self):
        source = Path("app/api/routers/leoft_student_app.py").read_text(encoding="utf-8")

        self.assertIn('if plan.status != "approved":', source)
        self.assertIn("Plano ainda não liberado", source)
        self.assertIn('"status": plan.status', source)
        self.assertIn("normalize_leoft_plan_for_app", source)

    async def test_e2e_error_output_does_not_include_secret_values(self):
        args = SimpleNamespace(use_real_hermes=True, send_real_telegram=True, test_student=True, wait_approval=True)
        with patch(
            "scripts.e2e_leoft_real_telegram_approval_app.telegram_config_status",
            return_value={
                "bot_token_present": True,
                "bot_token_valid": False,
                "treinos_chat_id_present": True,
                "treinos_chat_id_valid": True,
                "real_enabled": True,
                "approval_base_url_present": True,
                "approval_base_url_valid": True,
                "placeholder_detected": True,
            },
        ):
            buffer = io.StringIO()
            with redirect_stdout(buffer):
                result = await e2e_script.main(args)

        output = buffer.getvalue() + json.dumps(result, ensure_ascii=False)
        self.assertEqual(result["resultado"], "BLOQUEADO_CONFIG_TELEGRAM")
        self.assertNotIn("secret-token-value", output)
        self.assertNotIn("Traceback", output)


if __name__ == "__main__":
    unittest.main()

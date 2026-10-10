from __future__ import annotations

import io
import json
import os
import unittest
import uuid
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services.leoft_hermes_hmac import build_hermes_hmac_headers
from app.services.leoft_hermes_review_client import (
    DEFAULT_REVIEW_ENDPOINT,
    HermesReviewClient,
    HermesReviewConfig,
    HermesReviewClientError,
    build_review_idempotency_key,
    build_training_ready_for_review_payload,
    classify_review_response,
    count_plan_structure,
    dry_run_review_result,
    training_summary_from_plan,
)
from app.services.leoft_training_service import update_telegram_status
from db.models import LeoFTTrainingPlan


APPROVAL_URL = "https://agenteos.ferriolimidias.com.br/api/public/leoft-training/approve?token=leoft_approve.11111111-1111-4111-8111-111111111111.secret"


def _plan(extra_exercise: bool = False) -> dict:
    months = []
    for month in (1, 2, 3):
        exercises = [
            {
                "exerciseId": f"ex-{month}-1",
                "name": "Agachamento",
                "category": "Pernas",
                "muscleGroup": "Quadriceps",
                "equipment": "Livre",
                "prescriptionMode": "simple",
                "sets": "3",
                "reps": "10",
                "duration": "",
                "distance": "",
                "rest": "60s",
                "intensity": "moderada",
                "tempo": "",
                "notes": "Sem dor",
                "seriesDetails": [{"series": 1, "reps": "10", "intensity": "moderada"}],
                "blocks": [],
                "media": {"type": "none", "url": ""},
                "instructions": ["Controlar descida."],
            }
        ]
        if extra_exercise and month == 1:
            exercises.append(dict(exercises[0], exerciseId="ex-extra", name="Remada"))
        months.append(
            {
                "monthNumber": month,
                "title": f"Mes {month}",
                "weeks": [
                    {
                        "weekNumber": month,
                        "focus": "Base",
                        "workouts": [
                            {
                                "workoutId": f"m{month}w1",
                                "title": "Treino A",
                                "type": "strength",
                                "exercises": exercises,
                                "blocks": [{"phase": "aquecimento", "duration": "5 min"}],
                            }
                        ],
                    }
                ],
            }
        )
    return {
        "schemaVersion": "1.0",
        "planId": str(uuid.uuid4()),
        "studentName": "Aluno Teste LeoFT",
        "status": "pending_approval",
        "durationMonths": 3,
        "durationWeeks": 12,
        "productName": "Método Híbrido LeoFT",
        "months": months,
        "notes": ["Teste"],
        "progression": {"rule": "semanal"},
    }


def _payload(plan_json: dict | None = None, approval_url: str = APPROVAL_URL) -> dict:
    return build_training_ready_for_review_payload(
        empresa_id="a7384cb4-98b2-4750-94bc-3b329d35156d",
        lead_id="11111111-1111-4111-8111-111111111111",
        anamnese_id="leoft-anamnese:11111111-1111-4111-8111-111111111111",
        plan_id="22222222-2222-4222-8222-222222222222",
        student={"name": "Aluno Teste LeoFT"},
        training_summary=training_summary_from_plan(plan_json or _plan(), {"mainGoal": "Hipertrofia"}),
        plan_json=plan_json or _plan(),
        approval_url=approval_url,
        test_mode=True,
    )


class LeoFTHermesReviewClientTest(unittest.IsolatedAsyncioTestCase):
    def test_payload_keeps_complete_plan_json_and_counts(self):
        plan = _plan(extra_exercise=True)
        payload = _payload(plan)

        self.assertEqual(payload["plan_json"], plan)
        self.assertEqual(payload["plan_json"]["months"][0]["weeks"][0]["workouts"][0]["exercises"][1]["name"], "Remada")
        self.assertEqual(count_plan_structure(plan), {"months": 3, "weeks": 3, "workouts": 3, "exercises": 4})

    def test_approval_url_validation_blocks_wrong_domain(self):
        with self.assertRaisesRegex(HermesReviewClientError, "approval_url_invalid_host"):
            _payload(approval_url="https://evil.example/api/public/leoft-training/approve?token=abc")

    def test_approval_url_validation_requires_https_path_and_token(self):
        bad_urls = [
            "http://agenteos.ferriolimidias.com.br/api/public/leoft-training/approve?token=abc",
            "https://agenteos.ferriolimidias.com.br/api/public/leoft-training/wrong?token=abc",
            "https://agenteos.ferriolimidias.com.br/api/public/leoft-training/approve",
        ]
        for url in bad_urls:
            with self.assertRaises(HermesReviewClientError):
                _payload(approval_url=url)

    def test_idempotency_key_is_stable_and_changes_when_plan_changes(self):
        plan = _plan()
        same = json.loads(json.dumps(plan))
        changed = _plan(extra_exercise=True)

        key_a = build_review_idempotency_key(plan_id="plan-1", plan_json=plan)
        key_b = build_review_idempotency_key(plan_id="plan-1", plan_json=same)
        key_c = build_review_idempotency_key(plan_id="plan-1", plan_json=changed)

        self.assertEqual(key_a, key_b)
        self.assertNotEqual(key_a, key_c)
        self.assertNotIn("leoft_approve", key_a)

    def test_hmac_headers_use_review_event_and_review_idempotency_key(self):
        payload = _payload()
        with patch.dict(os.environ, {"HERMES_LEOFT_HMAC_SECRET": "fake-local-secret"}, clear=True):
            headers = build_hermes_hmac_headers(payload)

        self.assertEqual(headers["X-AgenteOS-Event"], "leoft.training_ready_for_review")
        self.assertEqual(headers["X-AgenteOS-Idempotency-Key"], payload["idempotency_key"])

    def test_endpoint_review_is_used_and_secrets_are_not_printed(self):
        response = MagicMock()
        response.status = 200
        response.read.return_value = json.dumps(
            {
                "sent": True,
                "telegram_message_id": 43,
                "review_chat_id_masked": "-100...8649",
                "group_name": "Treinos",
            }
        ).encode()
        response.__enter__.return_value = response
        payload = _payload()
        config = HermesReviewConfig(base_url="https://app.leoft.com.br", review_endpoint=DEFAULT_REVIEW_ENDPOINT, timeout_seconds=10)

        with patch.dict(os.environ, {"HERMES_LEOFT_HMAC_SECRET": "fake-local-secret"}, clear=True), patch(
            "app.services.leoft_hermes_review_client.urlopen", return_value=response
        ) as mocked_urlopen, redirect_stdout(io.StringIO()) as stdout:
            result = HermesReviewClient(config=config).send_training_ready_for_review(payload)

        request = mocked_urlopen.call_args.args[0]
        self.assertEqual(request.full_url, "https://app.leoft.com.br/api/internal/hermes/events/leoft/training-ready-for-review")
        self.assertEqual(result["status"], "sent")
        self.assertEqual(result["telegram_message_id"], 43)
        self.assertNotIn("fake-local-secret", stdout.getvalue())
        self.assertNotIn("leoft_approve.", stdout.getvalue())

    async def test_update_success_and_failure_status_preserve_pending_and_token_hash(self):
        plan = LeoFTTrainingPlan(id=uuid.uuid4(), empresa_id=uuid.uuid4(), lead_id=uuid.uuid4(), status="pending_approval", telegram_payload={"approval_token_hash": "hash"})

        await update_telegram_status(None, plan, classify_review_response({"sent": True, "telegram_message_id": 43}, _payload()))
        self.assertEqual(plan.telegram_status, "sent")
        self.assertEqual(plan.status, "pending_approval")
        self.assertEqual(plan.telegram_payload["approval_token_hash"], "hash")
        self.assertNotIn("approval_url", plan.telegram_payload)

        await update_telegram_status(None, plan, dry_run_review_result(payload=_payload()))
        self.assertEqual(plan.telegram_status, "dry_run")
        self.assertEqual(plan.status, "pending_approval")

    def test_agentos_payment_flow_does_not_import_direct_telegram_notifier(self):
        source = Path("app/services/leoft_payment_service.py").read_text(encoding="utf-8")
        self.assertNotIn("TelegramTrainingApprovalNotifier", source)
        self.assertNotIn("LEOFT_TELEGRAM_BOT_TOKEN", source)
        self.assertNotIn("LEOFT_TELEGRAM_TREINOS_CHAT_ID", source)


if __name__ == "__main__":
    unittest.main()

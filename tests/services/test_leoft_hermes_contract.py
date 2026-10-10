from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.leoft_hermes_contract import (
    build_hermes_leoft_payload_from_lead,
    normalize_hermes_response_to_plan_json,
    validate_hermes_request_payload,
)


def _valid_payload() -> dict:
    return {
        "event": "leoft.payment_approved",
        "schemaVersion": "1.0",
        "source": "agentos",
        "empresa_id": "a7384cb4-98b2-4750-94bc-3b329d35156d",
        "lead_id": "11111111-1111-4111-8111-111111111111",
        "anamnese_id": "leoft-anamnese:11111111-1111-4111-8111-111111111111",
        "checkout_reference": "leoft:checkout:123",
        "payment_id": "pay_123",
        "amount": 97,
        "currency": "BRL",
        "payment_provider": "mercado_pago",
        "payment_status": "approved_simulated",
        "app_access": {"access_link_created": True},
        "student": {"name": "Aluno LeoFT", "email": "aluno@example.com", "whatsapp": "5554999900000"},
        "anamnese": {"personalData": {}, "trainingProfile": {}, "status": {}},
    }


class LeoFTHermesContractTest(unittest.TestCase):
    def test_validate_hermes_request_payload_accepts_empresa_lead_and_anamnese_id(self):
        payload = validate_hermes_request_payload(_valid_payload())

        self.assertEqual(payload["empresa_id"], "a7384cb4-98b2-4750-94bc-3b329d35156d")
        self.assertEqual(payload["lead_id"], "11111111-1111-4111-8111-111111111111")
        self.assertEqual(payload["anamnese_id"], "leoft-anamnese:11111111-1111-4111-8111-111111111111")

    def test_validate_hermes_request_payload_rejects_missing_empresa_id(self):
        payload = _valid_payload()
        payload.pop("empresa_id")

        with self.assertRaisesRegex(ValueError, "missing_empresa_id"):
            validate_hermes_request_payload(payload)

    def test_validate_hermes_request_payload_rejects_missing_anamnese_id(self):
        payload = _valid_payload()
        payload.pop("anamnese_id")

        with self.assertRaisesRegex(ValueError, "missing_anamnese_id"):
            validate_hermes_request_payload(payload)

    def test_builder_uses_stable_anamnese_id_not_raw_token(self):
        lead_id = "11111111-1111-4111-8111-111111111111"
        lead = SimpleNamespace(
            id=lead_id,
            empresa_id="a7384cb4-98b2-4750-94bc-3b329d35156d",
            nome_contato="Aluno LeoFT",
            telefone_contato="5554999900000",
            dados_adicionais={
                "email": "aluno@example.com",
                "anamneses": {
                    "leoft": {
                        "submitted": True,
                        "respostas": {
                            "personalData": {"name": "Aluno LeoFT", "email": "aluno@example.com"},
                            "trainingProfile": {},
                            "status": {},
                        },
                    }
                },
            },
        )
        token_hash = "hash-seguro-que-nao-deve-virar-anamnese-id"

        payload = build_hermes_leoft_payload_from_lead(
            lead=lead,
            payment={"payment_id": "pay_123", "reference_hash": token_hash, "status": "approved_simulated"},
        )

        self.assertEqual(payload["anamnese_id"], f"leoft-anamnese:{lead_id}")
        self.assertNotEqual(payload["anamnese_id"], token_hash)

    def test_normalize_hermes_response_accepts_real_generated_plan_as_pending_approval(self):
        response = {
            "status": "ok",
            "processed": True,
            "generated": True,
            "called_external_services": False,
            "plan_id": 24,
            "plan_json": _remote_weeks_plan(status="generated", plan_id="24"),
        }

        plan = normalize_hermes_response_to_plan_json(response)

        self.assertEqual(plan["status"], "pending_approval")
        self.assertEqual(plan["hermesPlanId"], "24")
        self.assertRegex(plan["planId"], r"^[0-9a-f-]{36}$")
        self.assertEqual(plan["durationMonths"], 3)
        self.assertEqual(len(plan["months"]), 3)
        self.assertEqual(plan["months"][0]["weeks"][0]["workouts"][0]["exercises"][0]["exerciseId"], "mus_supino_halteres")


def _valid_plan(*, status: str, plan_id: str) -> dict:
    return {
        "schemaVersion": "1.0",
        "planId": plan_id,
        "studentName": "Aluno LeoFT",
        "status": status,
        "durationMonths": 3,
        "productName": "Método Híbrido LeoFT",
        "months": [
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
                                "title": "Treino",
                                "type": "strength",
                                "exercises": [
                                    {
                                        "exerciseId": f"ex-{month}",
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
                                        "intensity": "",
                                        "tempo": "",
                                        "notes": "",
                                        "seriesDetails": [],
                                        "blocks": [],
                                        "media": {"type": "none", "url": ""},
                                        "instructions": [],
                                    }
                                ],
                                "blocks": [],
                            }
                        ],
                    }
                ],
            }
            for month in (1, 2, 3)
        ],
        "notes": [],
        "safetyWarnings": [],
    }


def _remote_weeks_plan(*, status: str, plan_id: str) -> dict:
    plan = _valid_plan(status=status, plan_id=plan_id)
    plan.pop("durationMonths")
    plan.pop("months")
    plan["durationWeeks"] = 12
    plan["aluno"] = {"name": "Aluno LeoFT"}
    plan["phases"] = [
        {"title": "Base", "weeks": [1, 2, 3, 4]},
        {"title": "Progressao", "weeks": [5, 6, 7, 8]},
        {"title": "Consolidacao", "weeks": [9, 10, 11, 12]},
    ]
    plan["weeks"] = [
        {
            "week_number": week,
            "focus": "tecnica",
            "sessions": [
                {
                    "session_id": f"w{week:02d}_s01",
                    "title": "Treino A",
                    "type": "musculacao",
                    "exercises": [
                        {
                            "exercise_id": "mus_supino_halteres",
                            "name": "Supino com halteres",
                            "group": "peito",
                            "sets": 3,
                            "reps": "10-12",
                            "rest": "45-75s",
                            "intensity": "moderada",
                            "instructions": "Controlar a descida.",
                            "gif_url": "",
                        }
                    ],
                }
            ],
        }
        for week in range(1, 13)
    ]
    return plan


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
import uuid
from pathlib import Path

from scripts.verificar_leoft_hermes_remoto import _synthetic_payload


class LeoFTHermesRemoteScriptTest(unittest.TestCase):
    def test_synthetic_payload_contains_empresa_lead_and_anamnese_id(self):
        payload = _synthetic_payload()

        self.assertEqual(payload["empresa_id"], "a7384cb4-98b2-4750-94bc-3b329d35156d")
        self.assertIsInstance(payload["lead_id"], str)
        uuid.UUID(payload["lead_id"])
        self.assertIsInstance(payload["anamnese_id"], str)
        self.assertTrue(payload["anamnese_id"].startswith("leoft-anamnese-fake:"))
        self.assertEqual(payload["checkout_reference"].split(":")[:2], ["leoft", "fake"])

    def test_synthetic_payload_anamnese_id_does_not_use_raw_token(self):
        payload = _synthetic_payload()

        self.assertNotIn("token", payload["anamnese_id"].lower())
        self.assertNotEqual(payload["anamnese_id"], payload["checkout_reference"])

    def test_e2e_error_block_does_not_read_lead_id_after_rollback(self):
        source = Path("scripts/e2e_leoft_agentos_anamnese_pagamento_hermes_real.py").read_text(encoding="utf-8")
        except_start = source.index("        except (HermesTrainingClientError, ValueError) as exc:")
        except_end = source.index("        plan = await session.scalar", except_start)
        except_block = source[except_start:except_end]

        self.assertIn("lead_id_str = str(lead.id)", source[:except_start])
        self.assertIn("anamnese_id_str = f\"leoft-anamnese:{lead_id_str}\"", source[:except_start])
        self.assertIn("await session.rollback()", except_block)
        self.assertNotIn("str(lead.id)", except_block)
        self.assertIn('"lead_id": lead_id_str', except_block)
        self.assertIn('"anamnese_id": _mask_anamnese_id(anamnese_id_str)', except_block)


if __name__ == "__main__":
    unittest.main()

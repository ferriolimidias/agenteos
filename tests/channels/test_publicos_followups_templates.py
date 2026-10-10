import unittest

from app.services.campaign_audience_service import (
    mask_phone,
    normalize_phone,
    parse_csv_bytes,
    preview_import_csv,
    safe_cell,
)
from app.services.campaign_central_policy import PolicyInput, evaluate_message_policy
from app.services.campaign_central_service import validate_template_payload


class PublicosFollowupsTemplatesTests(unittest.IsolatedAsyncioTestCase):
    async def test_csv_preview_detects_headers_and_masks_phone(self):
        preview = await preview_import_csv("nome,telefone\nAna,11999998888\n".encode())
        self.assertEqual(preview["total_rows"], 1)
        self.assertEqual(preview["mapping"]["name"], "nome")
        self.assertIn("***", preview["sample_rows"][0]["phone"])

    def test_csv_injection_is_sanitized(self):
        self.assertEqual(safe_cell("=cmd"), "'=cmd")
        headers, rows, _ = parse_csv_bytes("nome,telefone\n+Ana,11999998888\n".encode())
        self.assertEqual(headers, ["nome", "telefone"])
        self.assertEqual(rows[0]["nome"], "'+Ana")

    def test_phone_normalization_does_not_invent_country_without_default(self):
        self.assertEqual(normalize_phone("9999", default_country_code=None), (None, False))
        self.assertEqual(normalize_phone("11999998888", default_country_code="55")[0], "+5511999998888")
        self.assertTrue(mask_phone("+5511999998888").startswith("+55"))

    def test_policy_separates_ad_window_message_mode_and_cost(self):
        decision = evaluate_message_policy(
            PolicyInput(
                provider="meta_whatsapp",
                channel="whatsapp",
                origin="ctwa",
                category="marketing",
            )
        )
        self.assertIn(decision["ad_entry_window"], {"unknown", "active", "expired", "not_applicable"})
        self.assertIn(decision["message_mode"], {"free_form_allowed", "template_required", "blocked", "unknown"})
        self.assertIn(decision["cost_estimation_state"], {"estimated_free", "estimated_chargeable", "unknown"})

    def test_template_variables_use_represents_without_examples_in_main_editor(self):
        payload = {
            "technical_name": "retorno_preco_v1",
            "language": "pt_BR",
            "category": "UTILITY",
            "blocks": {"body": {"text": "Olá, {{1}}! Proposta de {{2}}."}},
            "variables": [{"position": 1, "represents": "Nome"}, {"position": 2, "represents": "Produto"}],
        }
        validation = validate_template_payload(payload)
        self.assertTrue(validation["valid"])
        self.assertEqual(validation["variables"][0]["represents"], "Nome")


if __name__ == "__main__":
    unittest.main()

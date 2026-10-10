import unittest
from datetime import datetime, timedelta
from io import BytesIO

from app.services.campaign_central_policy import PolicyInput, evaluate_message_policy, estimate_cost
from app.services.campaign_central_service import (
    build_meta_template_create_payload,
    build_meta_template_send_payload,
    extract_numeric_variables,
    prepare_remote_template_action,
    template_requires_header_image,
    validate_campaign_image_upload,
    validate_template_payload,
    validate_template_payload_for_meta_create,
    validate_template_header_image_upload,
)
from app.services.meta.image_validation import ImageValidationError
from app.services.meta_templates import parse_template_components


class PhaseCCampaignCentralTests(unittest.TestCase):
    def test_policy_keeps_meta_rules_central_and_configurable(self):
        decision = evaluate_message_policy(
            PolicyInput(
                provider="meta_whatsapp",
                channel="whatsapp",
                origin="ctwa",
                category="marketing",
                country="BR",
                execution_at=datetime(2026, 7, 21, 12, 0, 0),
                last_inbound_at=datetime(2026, 7, 21, 11, 0, 0),
                ad_click_at=datetime(2026, 7, 20, 12, 0, 0),
            )
        )
        self.assertEqual(decision["decision"], "free_form_probably_allowed")
        self.assertEqual(decision["template_requirement"], "not_required_for_free_form")
        self.assertTrue(decision["effective_rules"]["service_window_hours"]["configurable"])
        self.assertFalse(decision["effective_rules"]["service_window_hours"]["confirmed"])

    def test_policy_requires_template_when_window_probably_closed(self):
        decision = evaluate_message_policy(
            PolicyInput(
                provider="meta_whatsapp",
                channel="whatsapp",
                category="utility",
                execution_at=datetime(2026, 7, 21, 12, 0, 0),
                last_inbound_at=datetime(2026, 7, 19, 12, 0, 0),
            )
        )
        self.assertEqual(decision["decision"], "template_probably_required")
        self.assertEqual(decision["template_requirement"], "probably_required")

    def test_template_payload_requires_visual_variables_represents(self):
        payload = {
            "technical_name": "consulta_confirmacao",
            "language": "pt_BR",
            "category": "UTILITY",
            "blocks": {"body": {"text": "Olá, {{1}}. Sua consulta é {{2}}."}},
            "variables": [{"position": 1, "represents": "Nome"}],
        }
        validation = validate_template_payload(payload)
        self.assertFalse(validation["valid"])
        self.assertIn(2, validation["variable_positions"])

    def test_template_payload_accepts_complete_components_and_preserves_unknown(self):
        payload = {
            "technical_name": "oferta_julho",
            "language": "pt_BR",
            "category": "MARKETING",
            "blocks": {
                "header": {"kind": "image", "example": {"header_handle": ["mock"]}},
                "body": {"text": "Olá, {{1}}."},
                "unknown_components": [{"type": "CAROUSEL", "cards": []}],
            },
            "variables": [{"position": 1, "represents": "Nome", "example": "Ana"}],
        }
        validation = validate_template_payload(payload)
        self.assertTrue(validation["valid"])
        self.assertEqual(validation["components"][-1]["type"], "CAROUSEL")

    def test_remote_actions_keep_non_create_actions_mocked_and_delete_blocked(self):
        submit = prepare_remote_template_action("sync_template_status", {"token": "secret"})
        delete = prepare_remote_template_action("delete_template", {})
        self.assertTrue(submit["mocked"])
        self.assertFalse(submit["external_call"])
        self.assertFalse(delete["allowed"])

    def test_meta_create_requires_examples_and_builds_body_example(self):
        payload = {
            "technical_name": "retomada_interesse",
            "language": "pt_BR",
            "category": "MARKETING",
            "blocks": {"body": {"text": "Oi, {{1}}!"}},
            "variables": [{"position": 1, "represents": "Primeiro nome"}],
        }
        validation = validate_template_payload_for_meta_create(payload)
        self.assertFalse(validation["valid"])
        payload["variables"][0]["example"] = "José"
        validation = validate_template_payload_for_meta_create(payload)
        self.assertTrue(validation["valid"])
        meta_payload = build_meta_template_create_payload(payload, validation)
        self.assertEqual(meta_payload["category"], "MARKETING")
        self.assertEqual(meta_payload["language"], "pt_BR")
        self.assertEqual(meta_payload["components"][0]["example"], {"body_text": [["José"]]})

    def test_image_header_template_requires_header_handle_and_builds_payload(self):
        payload = {
            "technical_name": "oferta_imagem",
            "language": "pt_BR",
            "category": "MARKETING",
            "blocks": {"header": {"kind": "image"}, "body": {"text": "Oi, {{1}}!"}},
            "variables": [{"position": 1, "represents": "Primeiro nome", "example": "Ana"}],
        }
        validation = validate_template_payload_for_meta_create(payload)
        self.assertFalse(validation["valid"])
        self.assertIn("header_image", {error["field"] for error in validation["errors"]})
        payload["blocks"]["header"]["example"] = {"header_handle": ["handle-123"]}
        validation = validate_template_payload_for_meta_create(payload)
        self.assertTrue(validation["valid"])
        meta_payload = build_meta_template_create_payload(payload, validation)
        self.assertEqual(meta_payload["components"][0]["type"], "HEADER")
        self.assertEqual(meta_payload["components"][0]["format"], "IMAGE")
        self.assertEqual(meta_payload["components"][0]["example"], {"header_handle": ["handle-123"]})

    def test_image_validation_accepts_jpeg_png_and_rejects_size_mime(self):
        from PIL import Image

        out = BytesIO()
        Image.new("RGB", (10, 10), color=(255, 0, 0)).save(out, format="PNG")
        png = out.getvalue()
        image = validate_template_header_image_upload(png, declared_mime="image/png", original_name="x.png")
        self.assertEqual(image["mime_type"], "image/png")
        image = validate_campaign_image_upload(png, declared_mime="image/png", original_name="x.png")
        self.assertEqual(image["purpose"], "campaign_header_image")
        with self.assertRaises(ImageValidationError):
            validate_template_header_image_upload(png, declared_mime="image/gif", original_name="x.gif")
        with self.assertRaises(ImageValidationError):
            validate_template_header_image_upload(b"x" * (5 * 1024 * 1024 + 1), declared_mime="image/png", original_name="x.png")

    def test_campaign_image_payload_uses_header_image_id_separate_from_body(self):
        components = [
            {"type": "HEADER", "format": "IMAGE"},
            {"type": "BODY", "text": "Oi, {{1}}"},
        ]
        parsed = parse_template_components(components)
        self.assertTrue(template_requires_header_image(components))
        payload = build_meta_template_send_payload(
            template_name="campanha_imagem",
            language="pt_BR",
            parsed_template=parsed,
            parameters={"header_image": "media-123", "1": "Ana"},
        )
        header = next(item for item in payload["components"] if item["type"] == "header")
        body = next(item for item in payload["components"] if item["type"] == "body")
        self.assertEqual(header["parameters"][0], {"type": "image", "image": {"id": "media-123"}})
        self.assertEqual(body["parameters"][0], {"type": "text", "text": "Ana"})

    def test_static_template_payload_is_minimal_without_components(self):
        parsed = parse_template_components([{"type": "BODY", "text": "Mensagem aprovada sem variáveis."}])
        payload = build_meta_template_send_payload(
            template_name="marketing_simples",
            language="pt_BR",
            parsed_template=parsed,
            parameters={},
        )
        self.assertEqual(payload, {"name": "marketing_simples", "language": {"code": "pt_BR"}})

    def test_static_url_button_send_does_not_add_runtime_button_component(self):
        parsed = parse_template_components([
            {"type": "BODY", "text": "Mensagem aprovada."},
            {"type": "BUTTONS", "buttons": [{"type": "URL", "text": "Abrir", "url": "https://example.com"}]},
        ])
        payload = build_meta_template_send_payload(
            template_name="marketing_botao_estatico",
            language="pt_BR",
            parsed_template=parsed,
            parameters={},
        )
        self.assertNotIn("components", payload)

    def test_static_media_template_payload_uses_header_media_id_only(self):
        parsed = parse_template_components([
            {"type": "HEADER", "format": "IMAGE"},
            {"type": "BODY", "text": "Mensagem aprovada sem variáveis."},
            {"type": "BUTTONS", "buttons": [{"type": "URL", "text": "Abrir", "url": "https://example.com"}]},
        ])
        payload = build_meta_template_send_payload(
            template_name="marketing_imagem_estatica",
            language="pt_BR",
            parsed_template=parsed,
            parameters={"header_image": "media-123"},
        )
        self.assertEqual(payload["components"], [{"type": "header", "parameters": [{"type": "image", "image": {"id": "media-123"}}]}])

    def test_cost_states(self):
        self.assertEqual(estimate_cost(rate=None, currency="BRL", free=False)["status"], "unknown")
        self.assertEqual(estimate_cost(rate=0.1, currency="BRL", free=False, charged=True)["status"], "derived")
        self.assertEqual(estimate_cost(rate=0.1, currency="BRL", free=True)["status"], "free")

    def test_extract_numeric_variables(self):
        self.assertEqual(extract_numeric_variables("A {{2}} B {{1}} C {{2}}"), [1, 2])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import unittest
from types import SimpleNamespace

from app.services.campaign_central_service import (
    build_meta_template_create_payload,
    build_meta_template_send_payload,
    normalize_campaign_variable_bindings,
    resolve_campaign_template_parameters_for_lead,
    template_required_parameter_keys,
    validate_template_draft_payload,
    validate_template_payload,
    validate_template_payload_for_meta_create,
)
from app.services.meta_templates import parse_template_components, render_template_preview


class CampaignTemplateVariableTests(unittest.TestCase):
    def real_form_payload(self, *, url: str = "https://example.com/grupo") -> dict:
        body = (
            "Olá! 💛\n\n"
            "Temos uma atualização referente a *{{1}}*.\n\n"
            "📅 *Data:* {{2}}\n"
            "⏰ *Horário:* {{3}}\n"
            "📍 *Local:* {{4}}\n\n"
            "{{5}}\n\n"
            "*Informações importantes:*\n"
            "{{6}}\n"
            "{{7}}\n\n"
            "Até lá!"
        )
        return {
            "internal_name": "Envio dança circular 06/09",
            "technical_name": "circular",
            "language": "pt_BR",
            "category": "UTILITY",
            "blocks": {
                "header": {"kind": "image", "example": {"header_handle": ["HEADER_HANDLE_MOCK"]}},
                "body": {"text": body},
                "footer": {"text": ""},
                "buttons": [{"type": "URL", "text": "Entrar no grupo", "url": url}],
            },
            "variables": [
                {"position": 1, "component": "BODY", "represents": "Evento", "example": "Dança Circular"},
                {"position": 2, "component": "BODY", "represents": "Data", "example": "06/09"},
                {"position": 3, "component": "BODY", "represents": "Horário", "example": "15h"},
                {"position": 4, "component": "BODY", "represents": "Endereço", "example": "Rua das hortênsias 1530"},
                {"position": 5, "component": "BODY", "represents": "Informação 1", "example": "Leve água"},
                {"position": 6, "component": "BODY", "represents": "Informação 2", "example": "Roupas confortáveis"},
                {"position": 7, "component": "BODY", "represents": "Informação 3", "example": "Alimentação leve"},
            ],
        }

    def test_real_form_draft_contract_header_image_variables_button(self):
        payload = self.real_form_payload(url="https://example.com/grupo")
        draft = validate_template_draft_payload(payload)
        self.assertTrue(draft["valid"], draft["errors"])
        self.assertEqual(draft["components"][0], {"type": "HEADER", "format": "IMAGE", "example": {"header_handle": ["HEADER_HANDLE_MOCK"]}})
        self.assertEqual(draft["components"][1]["text"], payload["blocks"]["body"]["text"])
        self.assertEqual(draft["variable_positions"], [1, 2, 3, 4, 5, 6, 7])

    def test_meta_create_exact_payload_for_real_form(self):
        payload = self.real_form_payload(url="https://example.com/grupo")
        validation = validate_template_payload_for_meta_create(payload)
        self.assertTrue(validation["valid"], validation["errors"])
        meta_payload = validation["meta_payload"]
        self.assertEqual(meta_payload["name"], "circular")
        self.assertEqual(meta_payload["language"], "pt_BR")
        self.assertEqual(meta_payload["category"], "UTILITY")
        self.assertNotIn("parameter_format", meta_payload)
        self.assertEqual(meta_payload["components"][0]["example"], {"header_handle": ["HEADER_HANDLE_MOCK"]})
        self.assertEqual(
            meta_payload["components"][1]["example"],
            {"body_text": [["Dança Circular", "06/09", "15h", "Rua das hortênsias 1530", "Leve água", "Roupas confortáveis", "Alimentação leve"]]},
        )
        self.assertNotIn("represents", str(meta_payload))

    def test_validate_image_selected_no_handle(self):
        payload = self.real_form_payload(url="https://example.com/grupo")
        payload["blocks"]["header"] = {
            "kind": "image",
            "local_media": {
                "selected": True,
                "filename": "example.png",
                "mime_type": "image/png",
                "size": 263800,
            },
        }
        validation = validate_template_payload_for_meta_create(payload, require_remote_header_handle=False)
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertTrue(validation["readiness"]["header_image_local_ready"])
        self.assertFalse(validation["readiness"]["meta_submission_payload_ready"])
        self.assertIsNone(validation["meta_payload"])

    def test_validate_image_missing(self):
        payload = self.real_form_payload(url="https://example.com/grupo")
        payload["blocks"]["header"] = {"kind": "image"}
        validation = validate_template_payload_for_meta_create(payload, require_remote_header_handle=False)
        self.assertFalse(validation["valid"])
        self.assertIn("header_image", {error["field"] for error in validation["errors"]})
        self.assertIn("Selecione uma imagem de exemplo", " ".join(error["message"] for error in validation["errors"]))

    def test_validate_image_invalid_mime(self):
        payload = self.real_form_payload(url="https://example.com/grupo")
        payload["blocks"]["header"] = {
            "kind": "image",
            "local_media": {"selected": True, "filename": "bad.gif", "mime_type": "image/gif", "size": 1000},
        }
        validation = validate_template_payload_for_meta_create(payload, require_remote_header_handle=False)
        self.assertFalse(validation["valid"])
        self.assertIn("Use imagem JPEG ou PNG", " ".join(error["message"] for error in validation["errors"]))

    def test_validate_image_size(self):
        payload = self.real_form_payload(url="https://example.com/grupo")
        payload["blocks"]["header"] = {
            "kind": "image",
            "local_media": {"selected": True, "filename": "big.png", "mime_type": "image/png", "size": 5 * 1024 * 1024 + 1},
        }
        too_big = validate_template_payload_for_meta_create(payload, require_remote_header_handle=False)
        self.assertFalse(too_big["valid"])
        self.assertIn("5 MB", " ".join(error["message"] for error in too_big["errors"]))
        payload["blocks"]["header"]["local_media"]["size"] = 263800
        ok = validate_template_payload_for_meta_create(payload, require_remote_header_handle=False)
        self.assertTrue(ok["valid"], ok["errors"])

    def test_submit_requires_header_handle_after_local_validate(self):
        payload = self.real_form_payload(url="https://example.com/grupo")
        payload["blocks"]["header"] = {
            "kind": "image",
            "local_media": {"selected": True, "filename": "example.png", "mime_type": "image/png", "size": 263800},
        }
        validation = validate_template_payload_for_meta_create(payload)
        self.assertFalse(validation["valid"])
        self.assertIn("header_image", {error["field"] for error in validation["errors"]})

    def test_header_handle_not_media_id(self):
        payload = self.real_form_payload(url="https://example.com/grupo")
        payload["blocks"]["header"] = {"kind": "image", "example": {"header_handle": ["HEADER_HANDLE_MOCK"]}}
        validation = validate_template_payload_for_meta_create(payload)
        self.assertTrue(validation["valid"], validation["errors"])
        header = validation["meta_payload"]["components"][0]
        self.assertEqual(header["example"], {"header_handle": ["HEADER_HANDLE_MOCK"]})
        self.assertNotIn("media_id", str(header))

    def test_whatsapp_group_url_button_is_not_hardblocked_without_official_contract(self):
        payload = self.real_form_payload(url="https://chat.whatsapp.com/G2q63EVG4ix0JoWk0fxUp0")
        validation = validate_template_payload_for_meta_create(payload)
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertEqual(validation["meta_payload"]["components"][2]["buttons"][0]["url"], "https://chat.whatsapp.com/G2q63EVG4ix0JoWk0fxUp0")

    def test_template_backend_newlines_and_create_payload_preserve_text(self):
        body = "Olá! 👋\n\nLinha 2\nLinha 3\n\nFim"
        payload = {
            "technical_name": "texto_com_linhas",
            "language": "pt_BR",
            "category": "MARKETING",
            "blocks": {"body": {"text": body}},
            "variables": [],
        }
        validation = validate_template_payload_for_meta_create(payload)
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertEqual(validation["components"][0]["text"], body)
        meta_payload = build_meta_template_create_payload(payload, validation)
        self.assertEqual(meta_payload["components"][0]["text"], body)
        self.assertNotIn("parameter_format", meta_payload)

    def test_template_body_starts_with_parameter(self):
        payload = {
            "technical_name": "starts_with_parameter",
            "language": "pt_BR",
            "category": "UTILITY",
            "blocks": {"body": {"text": "{{1}} confirmou presença."}},
            "variables": [{"position": 1, "represents": "Nome", "example": "Maria"}],
        }
        validation = validate_template_payload_for_meta_create(payload)
        self.assertFalse(validation["valid"])
        self.assertIn("BODY_STARTS_WITH_PARAMETER", {error.get("code") for error in validation["errors"]})

    def test_template_body_ends_with_parameter(self):
        payload = {
            "technical_name": "ends_with_parameter",
            "language": "pt_BR",
            "category": "UTILITY",
            "blocks": {"body": {"text": "Confirmação para {{1}}"}},
            "variables": [{"position": 1, "represents": "Nome", "example": "Maria"}],
        }
        validation = validate_template_payload_for_meta_create(payload)
        self.assertFalse(validation["valid"])
        self.assertIn("BODY_ENDS_WITH_PARAMETER", {error.get("code") for error in validation["errors"]})
        self.assertIn("não pode terminar", " ".join(error["message"] for error in validation["errors"]))

    def test_template_body_valid_boundaries(self):
        payload = {
            "technical_name": "valid_boundaries",
            "language": "pt_BR",
            "category": "UTILITY",
            "blocks": {"body": {"text": "Olá {{1}}, sua confirmação está pronta."}},
            "variables": [{"position": 1, "represents": "Nome", "example": "Maria"}],
        }
        validation = validate_template_payload_for_meta_create(payload)
        self.assertTrue(validation["valid"], validation["errors"])

    def test_template_parameters_gap(self):
        payload = {
            "technical_name": "parameter_gap",
            "language": "pt_BR",
            "category": "UTILITY",
            "blocks": {"body": {"text": "Olá {{1}}, veja {{3}} agora."}},
            "variables": [
                {"position": 1, "represents": "Nome", "example": "Maria"},
                {"position": 3, "represents": "Info", "example": "dados"},
            ],
        }
        validation = validate_template_payload_for_meta_create(payload)
        self.assertFalse(validation["valid"])
        self.assertIn("variables", {error["field"] for error in validation["errors"]})

    def test_template_parameter_examples_count(self):
        payload = {
            "technical_name": "missing_example",
            "language": "pt_BR",
            "category": "UTILITY",
            "blocks": {"body": {"text": "Olá {{1}}, veja {{2}} agora."}},
            "variables": [
                {"position": 1, "represents": "Nome", "example": "Maria"},
                {"position": 2, "represents": "Info", "example": ""},
            ],
        }
        validation = validate_template_payload_for_meta_create(payload)
        self.assertFalse(validation["valid"])
        self.assertIn("BODY_PARAMETER_EXAMPLES_MISMATCH", {error.get("code") for error in validation["errors"]})

    def test_template_parameter_density_warning(self):
        payload = self.real_form_payload(url="https://example.com/grupo")
        validation = validate_template_payload_for_meta_create(payload)
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertIn("PARAMETER_DENSITY_REVIEW_RISK", {warning.get("code") for warning in validation["warnings"]})

    def test_current_circular_body_preflight_fails_when_ending_with_parameter(self):
        payload = self.real_form_payload(url="https://example.com/grupo")
        payload["blocks"]["body"]["text"] = payload["blocks"]["body"]["text"].removesuffix("\n\nAté lá!")
        validation = validate_template_payload_for_meta_create(payload)
        self.assertFalse(validation["valid"])
        self.assertTrue(validation["preflight"]["facts"]["body_ends_with_parameter"])
        self.assertIn("BODY_ENDS_WITH_PARAMETER", {error.get("code") for error in validation["errors"]})

    def test_corrected_circular_body_preflight_passes_with_static_suffix(self):
        payload = self.real_form_payload(url="https://example.com/grupo")
        validation = validate_template_payload_for_meta_create(payload)
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertFalse(validation["preflight"]["facts"]["body_ends_with_parameter"])
        self.assertEqual(validation["preflight"]["facts"]["body_parameter_count"], 7)

    def test_meta_official_simple_body_template(self):
        payload = {
            "technical_name": "simple_update",
            "language": "en_US",
            "category": "UTILITY",
            "blocks": {"body": {"text": "Your appointment is confirmed."}},
        }
        validation = validate_template_payload_for_meta_create(payload)
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertEqual(validation["meta_payload"], {
            "name": "simple_update",
            "language": "en_US",
            "category": "UTILITY",
            "components": [{"type": "BODY", "text": "Your appointment is confirmed."}],
        })

    def test_meta_official_body_variable_template(self):
        payload = {
            "technical_name": "order_delivery_update",
            "language": "en_US",
            "category": "UTILITY",
            "blocks": {"body": {"text": "Good news {{1}}! Your order #{{2}} is on its way."}},
            "variables": [
                {"position": 1, "represents": "Name", "example": "Mark"},
                {"position": 2, "represents": "Order", "example": "566701"},
            ],
        }
        validation = validate_template_payload_for_meta_create(payload)
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertEqual(validation["meta_payload"]["components"][0]["example"], {"body_text": [["Mark", "566701"]]})
        self.assertNotIn("parameter_format", validation["meta_payload"])

    def test_meta_official_image_header_template(self):
        payload = {
            "technical_name": "image_header_update",
            "language": "en_US",
            "category": "UTILITY",
            "blocks": {"header": {"kind": "image", "example": {"header_handle": ["handle-123"]}}, "body": {"text": "Image update."}},
        }
        validation = validate_template_payload_for_meta_create(payload)
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertEqual(validation["meta_payload"]["components"][0], {"type": "HEADER", "format": "IMAGE", "example": {"header_handle": ["handle-123"]}})

    def test_meta_official_quick_reply_template(self):
        payload = {
            "technical_name": "quick_reply_update",
            "language": "en_US",
            "category": "UTILITY",
            "blocks": {
                "body": {"text": "Choose an option."},
                "buttons": [{"type": "QUICK_REPLY", "text": "Stop Updates"}],
            },
        }
        validation = validate_template_payload_for_meta_create(payload)
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertEqual(validation["meta_payload"]["components"][1]["buttons"], [{"type": "QUICK_REPLY", "text": "Stop Updates"}])

    def test_meta_official_url_button_template(self):
        payload = {
            "technical_name": "url_button_update",
            "language": "en_US",
            "category": "UTILITY",
            "blocks": {
                "body": {"text": "Open the page."},
                "buttons": [{"type": "URL", "text": "Open", "url": "https://example.com/page"}],
            },
        }
        validation = validate_template_payload_for_meta_create(payload)
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertEqual(validation["meta_payload"]["components"][1]["buttons"][0]["url"], "https://example.com/page")

    def test_meta_official_phone_button_template(self):
        payload = {
            "technical_name": "phone_button_update",
            "language": "en_US",
            "category": "UTILITY",
            "blocks": {
                "body": {"text": "Call us."},
                "buttons": [{"type": "PHONE_NUMBER", "text": "Call", "phone_number": "+15551234567"}],
            },
        }
        validation = validate_template_payload_for_meta_create(payload)
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertEqual(validation["meta_payload"]["components"][1]["buttons"][0]["phone_number"], "+15551234567")

    def test_official_image_cta_fixture(self):
        payload = {
            "technical_name": "image_cta_fixture",
            "language": "pt_BR",
            "category": "UTILITY",
            "blocks": {
                "header": {"kind": "image", "example": {"header_handle": ["HEADER_HANDLE_MOCK"]}},
                "body": {"text": "Olá {{1}}, veja os detalhes do evento aqui."},
                "buttons": [
                    {"type": "URL", "text": "Ver detalhes", "url": "https://example.com/evento"},
                    {"type": "PHONE_NUMBER", "text": "Ligar", "phone_number": "+5551999999999"},
                ],
            },
            "variables": [{"position": 1, "represents": "Nome", "example": "Maria"}],
        }
        validation = validate_template_payload_for_meta_create(payload)
        self.assertTrue(validation["valid"], validation["errors"])
        components = validation["meta_payload"]["components"]
        self.assertEqual(components[0], {"type": "HEADER", "format": "IMAGE", "example": {"header_handle": ["HEADER_HANDLE_MOCK"]}})
        self.assertEqual(components[1]["example"], {"body_text": [["Maria"]]})
        self.assertEqual(components[2]["type"], "BUTTONS")

    def test_template_variables_with_newlines_and_seven_complete(self):
        body = (
            "Olá, {{1}}!\n\n"
            "Temos uma atualização referente a *{{2}}*.\n\n"
            "📅 *Data:* {{3}}\n"
            "⏰ *Horário:* {{4}}\n"
            "📍 *Local:* {{5}}\n\n"
            "{{6}}\n\n"
            "*Informações importantes:*\n"
            "{{7}}\n\n"
            "Até lá!"
        )
        payload = {
            "technical_name": "sete_variaveis",
            "language": "pt_BR",
            "category": "MARKETING",
            "blocks": {"body": {"text": body}},
            "variables": [
                {"position": 1, "component": "BODY", "represents": "Nome", "example": "Maria"},
                {"position": 2, "component": "BODY", "represents": "Tema", "example": "Dança Circular"},
                {"position": 3, "component": "BODY", "represents": "Data", "example": "06/09"},
                {"position": 4, "component": "BODY", "represents": "Horário", "example": "15h"},
                {"position": 5, "component": "BODY", "represents": "Endereço", "example": "Rua das hortênsias 1530"},
                {"position": 6, "component": "BODY", "represents": "Informação 1", "example": "Leve água"},
                {"position": 7, "component": "BODY", "represents": "Informação 2", "example": "Roupas confortáveis"},
            ],
        }
        draft = validate_template_draft_payload(payload)
        validation = validate_template_payload_for_meta_create(payload)
        self.assertTrue(draft["valid"], draft["errors"])
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertEqual(len(validation["variables"]), 7)
        self.assertEqual(validation["variable_positions"], [1, 2, 3, 4, 5, 6, 7])

    def test_complete_draft_roundtrip_contract_preserves_variables(self):
        payload = {
            "technical_name": "roundtrip_variaveis",
            "language": "pt_BR",
            "category": "MARKETING",
            "blocks": {"body": {"text": "Olá {{1}}\n\nLinha {{2}}"}},
            "variables": [
                {"position": 1, "component": "BODY", "represents": "Nome", "example": "Maria"},
                {"position": 2, "component": "BODY", "represents": "Linha", "example": "Final"},
            ],
        }
        draft = validate_template_draft_payload(payload)
        self.assertTrue(draft["valid"], draft["errors"])
        self.assertEqual(draft["components"][0]["text"], payload["blocks"]["body"]["text"])
        self.assertEqual(draft["variables"], [
            {"position": 1, "represents": "Nome", "component": "body", "example": "Maria"},
            {"position": 2, "represents": "Linha", "component": "body", "example": "Final"},
        ])

    def test_incomplete_draft_save_allowed_but_meta_validation_blocks(self):
        payload = {
            "technical_name": "rascunho_incompleto",
            "language": "pt_BR",
            "category": "MARKETING",
            "blocks": {
                "body": {"text": "Oi {{1}}"},
                "buttons": [{"type": "URL", "text": "Abrir", "url": ""}],
            },
            "variables": [{"position": 1, "component": "BODY", "represents": "", "example": ""}],
        }
        draft = validate_template_draft_payload(payload)
        meta = validate_template_payload_for_meta_create(payload)
        self.assertTrue(draft["valid"], draft["errors"])
        self.assertFalse(meta["valid"])
        self.assertIn("variable_1_example", {error["field"] for error in meta["errors"]})
        self.assertIn("button_0_url", {error["field"] for error in meta["errors"]})

    def test_sync_newline_roundtrip_parser(self):
        text = "Linha 1\n\nLinha 2"
        parsed = parse_template_components([{"type": "BODY", "text": text}])
        self.assertEqual(parsed.preview_components[0]["text"], text)

    def test_template_variable_filled_recognized_with_frontend_aliases(self):
        payload = {
            "technical_name": "oferta_variavel",
            "language": "pt_BR",
            "category": "MARKETING",
            "blocks": {"body": {"text": "Olá, {{1}}."}},
            "variables": [{"position": "1", "label": "Nome do contato", "sample": "José"}],
        }
        validation = validate_template_payload_for_meta_create(payload)
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertEqual(validation["variables"][0]["represents"], "Nome do contato")
        self.assertEqual(validation["variables"][0]["example"], "José")

    def test_template_variable_single_and_multiple(self):
        payload = {
            "technical_name": "oferta_duas_variaveis",
            "language": "pt_BR",
            "category": "UTILITY",
            "blocks": {"body": {"text": "Oi {{1}}, sua condição é {{2}}."}},
            "variables": [
                {"position": 1, "represents": "Nome", "example": "João"},
                {"position": 2, "represents": "Produto", "example": "Plano Premium"},
            ],
        }
        validation = validate_template_payload(payload)
        self.assertTrue(validation["valid"], validation["errors"])
        self.assertEqual(validation["variable_positions"], [1, 2])

    def test_template_variable_header_body_contract(self):
        components = [
            {"type": "HEADER", "format": "TEXT", "text": "Oi {{1}}"},
            {"type": "BODY", "text": "Oferta {{1}}"},
        ]
        parsed = parse_template_components(components)
        self.assertEqual(template_required_parameter_keys(parsed), ["header_1", "body_1"])

    def test_template_variable_sequence(self):
        payload = {
            "technical_name": "sequencia_invalida",
            "language": "pt_BR",
            "category": "MARKETING",
            "blocks": {"body": {"text": "Oi {{1}}, veja {{3}}."}},
            "variables": [
                {"position": 1, "represents": "Nome", "example": "Ana"},
                {"position": 3, "represents": "Produto", "example": "Plano"},
            ],
        }
        validation = validate_template_payload(payload)
        self.assertFalse(validation["valid"])
        self.assertIn("variables", {error["field"] for error in validation["errors"]})

    def test_template_variable_remove_stale_state(self):
        payload = {
            "technical_name": "stale",
            "language": "pt_BR",
            "category": "MARKETING",
            "blocks": {"body": {"text": "Oi {{1}}."}},
            "variables": [
                {"position": 1, "represents": "Nome", "example": "Ana"},
                {"position": 2, "represents": "Produto", "example": "Plano"},
            ],
        }
        validation = validate_template_payload(payload)
        self.assertFalse(validation["valid"])
        self.assertIn("variable_2", {error["field"] for error in validation["errors"]})


class CampaignTemplateButtonTests(unittest.TestCase):
    def test_template_button_quick_reply_url_phone_and_component_payload(self):
        payload = {
            "technical_name": "botoes_validos",
            "language": "pt_BR",
            "category": "MARKETING",
            "blocks": {
                "body": {"text": "Escolha uma opção."},
                "buttons": [
                    {"type": "URL", "text": "Ver plano", "url": "https://example.com/plano"},
                    {"type": "PHONE_NUMBER", "text": "Ligar", "phone_number": "+5551999999999"},
                ],
            },
        }
        validation = validate_template_payload(payload)
        self.assertTrue(validation["valid"], validation["errors"])
        buttons = next(component["buttons"] for component in validation["components"] if component["type"] == "BUTTONS")
        self.assertEqual(buttons[0]["type"], "URL")
        self.assertEqual(buttons[1]["type"], "PHONE_NUMBER")

    def test_template_button_dynamic_url(self):
        payload = {
            "technical_name": "botao_dinamico",
            "language": "pt_BR",
            "category": "MARKETING",
            "blocks": {
                "body": {"text": "Abra seu link."},
                "buttons": [{"type": "URL", "text": "Abrir", "url": "https://example.com/{{1}}", "example": "abc"}],
            },
        }
        validation = validate_template_payload_for_meta_create(payload)
        self.assertTrue(validation["valid"], validation["errors"])
        meta_payload = build_meta_template_create_payload(payload, validation)
        buttons = meta_payload["components"][1]["buttons"]
        self.assertEqual(buttons[0]["example"], ["abc"])

    def test_template_button_limits(self):
        payload = {
            "technical_name": "botoes_demais",
            "language": "pt_BR",
            "category": "MARKETING",
            "blocks": {
                "body": {"text": "Oi."},
                "buttons": [
                    {"type": "URL", "text": "A", "url": "https://a.test"},
                    {"type": "URL", "text": "B", "url": "https://b.test"},
                    {"type": "PHONE_NUMBER", "text": "C", "phone_number": "+5551999999999"},
                ],
            },
        }
        validation = validate_template_payload(payload)
        self.assertFalse(validation["valid"])
        self.assertIn("Use no máximo 2 botões", " ".join(error["message"] for error in validation["errors"]))

    def test_template_button_sync_roundtrip_parser(self):
        components = [{"type": "BUTTONS", "buttons": [{"type": "URL", "text": "Abrir", "url": "https://x.test/{{1}}"}]}]
        parsed = parse_template_components(components)
        self.assertTrue(parsed.has_buttons)
        self.assertTrue(parsed.has_dynamic_url)
        self.assertEqual(parsed.placeholders[0].name, "button_0_1")


class CampaignRuntimeBindingTests(unittest.TestCase):
    def test_campaign_fixed_and_contact_name_binding_materialization(self):
        parsed = parse_template_components([{"type": "BODY", "text": "Oi {{1}}, sua oferta é {{2}}."}])
        bindings = normalize_campaign_variable_bindings(
            {
                "body_1": {"type": "CONTACT_NAME"},
                "body_2": {"type": "FIXED_VALUE", "value": "Plano Premium"},
            }
        )
        lead = SimpleNamespace(nome_contato="Maria")
        values, missing = resolve_campaign_template_parameters_for_lead(parsed_template=parsed, bindings=bindings, lead=lead)
        self.assertFalse(missing)
        self.assertEqual(values, {"body_1": "Maria", "body_2": "Plano Premium"})

    def test_campaign_variable_missing_blocks_recipient(self):
        parsed = parse_template_components([{"type": "BODY", "text": "Oi {{1}}."}])
        bindings = normalize_campaign_variable_bindings({"body_1": {"type": "CONTACT_NAME"}})
        values, missing = resolve_campaign_template_parameters_for_lead(parsed_template=parsed, bindings=bindings, lead=SimpleNamespace(nome_contato=""))
        self.assertEqual(values, {})
        self.assertEqual(missing, ["body_1"])

    def test_campaign_per_recipient_payload_and_template_example_not_used_at_send(self):
        parsed = parse_template_components([{"type": "BODY", "text": "Oi {{1}}, sua oferta é {{2}}."}])
        payload_a = build_meta_template_send_payload(
            template_name="oferta",
            language="pt_BR",
            parsed_template=parsed,
            parameters={"body_1": "João", "body_2": "Plano Premium"},
        )
        payload_b = build_meta_template_send_payload(
            template_name="oferta",
            language="pt_BR",
            parsed_template=parsed,
            parameters={"body_1": "Maria", "body_2": "Plano Premium"},
        )
        self.assertEqual(payload_a["components"][0]["parameters"][0]["text"], "João")
        self.assertEqual(payload_b["components"][0]["parameters"][0]["text"], "Maria")
        self.assertNotIn("José", str(payload_b))

    def test_campaign_button_runtime_payload(self):
        parsed = parse_template_components(
            [
                {"type": "BODY", "text": "Oi {{1}}."},
                {"type": "BUTTONS", "buttons": [{"type": "URL", "text": "Abrir", "url": "https://x.test/{{1}}"}]},
            ]
        )
        payload = build_meta_template_send_payload(
            template_name="botao",
            language="pt_BR",
            parsed_template=parsed,
            parameters={"body_1": "Ana", "button_0_1": "abc123"},
        )
        button = next(component for component in payload["components"] if component["type"] == "button")
        self.assertEqual(button["sub_type"], "url")
        self.assertEqual(button["index"], "0")
        self.assertEqual(button["parameters"], [{"type": "text", "text": "abc123"}])

    def test_campaign_preview_newlines(self):
        components = [{"type": "BODY", "text": "Oi, {{1}}!\n\nSua condição:\n{{2}}"}]
        preview = render_template_preview(components, {"1": "Maria", "2": "Plano Premium"})
        self.assertEqual(preview["components"][0]["text"], "Oi, Maria!\n\nSua condição:\nPlano Premium")


if __name__ == "__main__":
    unittest.main()

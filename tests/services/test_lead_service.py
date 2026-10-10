import unittest

from app.services.lead_service import nome_contato_eh_fallback, precisa_atualizar_nome


class LeadServiceNameHeuristicsTests(unittest.TestCase):
    def test_generic_meta_fallback_is_treated_as_weak(self):
        self.assertTrue(nome_contato_eh_fallback("Contato Meta", "5511999999999"))
        self.assertTrue(precisa_atualizar_nome("Contato Meta", "5511999999999"))

    def test_visual_fallback_is_treated_as_weak(self):
        self.assertTrue(nome_contato_eh_fallback("Contato • final 1234", "5511999999999"))

    def test_manual_name_provenance_blocks_auto_overwrite(self):
        self.assertFalse(
            precisa_atualizar_nome(
                "Contato Meta",
                "5511999999999",
                {"nome_origem": "manual", "nome_editado_manual": True},
            )
        )

    def test_valid_name_is_not_treated_as_fallback(self):
        self.assertFalse(nome_contato_eh_fallback("Mariana Silva", "5511999999999"))


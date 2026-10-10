import unittest

from scripts.verificar_mensagem_historico_empresa_id import collect_calls


class MensagemHistoricoEmpresaIdTests(unittest.TestCase):
    def test_all_executable_constructors_set_empresa_id(self):
        calls = collect_calls()

        self.assertGreaterEqual(len(calls), 9)
        missing = [call for call in calls if not call["has_empresa_id"]]
        self.assertEqual(missing, [])

    def test_required_paths_are_covered(self):
        calls = collect_calls()
        by_function = {str(call["function"]) for call in calls}

        self.assertIn("_salvar_historico_saida_ia", by_function)
        self.assertIn("enviar_mensagem", by_function)
        self.assertIn("enviar_midia", by_function)
        self.assertIn("save_history_and_check_pause", by_function)
        self.assertIn("_persist_event", by_function)
        self.assertIn("_materialize_campaign_history", by_function)
        self.assertIn("_dispatch_followup_execution", by_function)
        self.assertIn("_persist_private_reply_outbound", by_function)
        self.assertIn("process_direct_message", by_function)


if __name__ == "__main__":
    unittest.main()

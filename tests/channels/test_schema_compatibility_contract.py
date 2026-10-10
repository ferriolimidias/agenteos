import unittest

from scripts.check_schema_compatibility import REQUIRED_COLUMNS, REQUIRED_CONSTRAINTS, find_missing_schema_items


class SchemaCompatibilityContractTests(unittest.TestCase):
    def test_conexoes_phase2_columns_are_required_before_startup(self):
        self.assertIn("channel", REQUIRED_COLUMNS["conexoes"])
        self.assertIn("provider", REQUIRED_COLUMNS["conexoes"])
        self.assertIn("phone_number_id", REQUIRED_COLUMNS["conexoes"])
        self.assertIn("uq_conexoes_empresa_id_id", REQUIRED_CONSTRAINTS)

    def test_new_database_fails_with_missing_tables(self):
        missing = find_missing_schema_items({}, set())

        self.assertIn("table:conexoes", missing)
        self.assertIn("table:channel_contacts", missing)
        self.assertIn("table:channel_conversations", missing)

    def test_pre_phase2_schema_fails_missing_conexoes_columns(self):
        missing = find_missing_schema_items(
            {
                "conexoes": {"id", "empresa_id", "tipo", "nome_instancia"},
                "channel_contacts": set(REQUIRED_COLUMNS["channel_contacts"]),
                "channel_conversations": set(REQUIRED_COLUMNS["channel_conversations"]),
            },
            set(REQUIRED_CONSTRAINTS),
        )

        self.assertIn("column:conexoes.channel", missing)
        self.assertIn("column:conexoes.provider", missing)

    def test_partial_schema_fails_missing_constraint(self):
        missing = find_missing_schema_items(
            {table: set(columns) for table, columns in REQUIRED_COLUMNS.items()},
            {"uq_conexoes_empresa_id_id"},
        )

        self.assertIn("constraint:uq_channel_contacts_empresa_id_id", missing)

    def test_updated_or_already_applied_schema_passes(self):
        missing = find_missing_schema_items(
            {table: set(columns) for table, columns in REQUIRED_COLUMNS.items()},
            set(REQUIRED_CONSTRAINTS),
        )

        self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()

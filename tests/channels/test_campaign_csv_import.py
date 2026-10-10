import inspect
import unittest

from app.services.campaign_audience_service import (
    ensure_tag,
    import_contacts_csv,
    normalize_phone,
    parse_csv_bytes,
    resolve_csv_mapping,
)


class CampaignCsvImportTests(unittest.TestCase):
    def test_semicolon_utf8_and_official_headers(self):
        headers, rows, encoding = parse_csv_bytes("nome;telefone\nMaria Teste;5554999990001\nAna Teste;5554999990002\n".encode())
        self.assertEqual(headers, ["nome", "telefone"])
        self.assertEqual(len(rows), 2)
        self.assertEqual(encoding, "utf-8-sig")
        mapping = resolve_csv_mapping(headers, {})
        self.assertEqual({mapping["name"], mapping["phone"]}, {"nome", "telefone"})

    def test_bom_crlf_and_quoted_fields(self):
        data = '\ufeffnome;telefone\r\n"Maria Teste";"5554999990001"\r\n"Ana Teste";"5554999990002"\r\n'.encode()
        headers, rows, encoding = parse_csv_bytes(data)
        self.assertEqual(headers, ["nome", "telefone"])
        self.assertEqual(rows[0]["nome"], "Maria Teste")
        self.assertEqual(len(rows), 2)
        self.assertEqual(encoding, "utf-8-sig")

    def test_comma_is_detected(self):
        headers, rows, _ = parse_csv_bytes(b"nome,telefone\nMaria,5554999990001\n")
        self.assertEqual(headers, ["nome", "telefone"])
        self.assertEqual(rows[0]["telefone"], "5554999990001")

    def test_invalid_structure_is_rejected_before_rows(self):
        headers, rows, _ = parse_csv_bytes(b"cliente;celular\nMaria;5554999990001\n")
        self.assertEqual(len(rows), 1)
        with self.assertRaisesRegex(ValueError, "colunas nome e telefone"):
            resolve_csv_mapping(headers, {})

    def test_header_whitespace_and_case_are_supported(self):
        headers, _, _ = parse_csv_bytes(b" Nome ; Telefone \nMaria;5554999990001\n")
        mapping = resolve_csv_mapping(headers, {})
        self.assertEqual({mapping["name"], mapping["phone"]}, {"Nome", "Telefone"})

    def test_normalized_brazilian_phones_are_accepted(self):
        self.assertEqual(normalize_phone("5554999990001"), ("+5554999990001", True))
        self.assertEqual(normalize_phone("54999999999", default_country_code="55"), ("+5554999999999", True))

    def test_empty_frontend_mapping_falls_back_to_headers(self):
        mapping = resolve_csv_mapping(["nome", "telefone"], {})
        self.assertEqual({mapping["name"], mapping["phone"]}, {"nome", "telefone"})

    def test_explicit_mapping_is_preserved(self):
        self.assertEqual(resolve_csv_mapping(["full_name", "mobile"], {"name": "full_name", "phone": "mobile"}), {"name": "full_name", "phone": "mobile"})

    def test_import_contract_does_not_create_conversation_or_send(self):
        source = inspect.getsource(import_contacts_csv)
        self.assertNotIn("ChannelConversation", source)
        self.assertNotIn("MensagemHistorico", source)
        self.assertNotIn("send_", source)
        self.assertIn('external_call": False', inspect.getsource(import_contacts_csv).replace("'", '"'))

    def test_import_contract_preserves_unknown_consent_without_fake_optin(self):
        source = inspect.getsource(import_contacts_csv)
        self.assertIn('consent_status = str(payload.get("consent_status") or "unknown")', source)
        self.assertIn('status=consent_status', source)
        self.assertIn('consented_at=datetime.utcnow() if consent_status == "opted_in" else None', source)

    def test_duplicate_existing_and_tenant_contracts_are_scoped(self):
        source = inspect.getsource(import_contacts_csv)
        self.assertIn("seen_keys", source)
        self.assertIn("CRMLead).where(CRMLead.empresa_id == empresa_id)", source)
        self.assertIn("TagCRM.id == tag_uuid, TagCRM.empresa_id == empresa_id", inspect.getsource(ensure_tag))


if __name__ == "__main__":
    unittest.main()

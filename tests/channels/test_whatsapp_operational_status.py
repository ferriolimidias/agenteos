import unittest
import uuid
from types import SimpleNamespace

from app.services.whatsapp_operational_status import resolve_whatsapp_operational_status_from_connections


def _conn(provider, status, **overrides):
    metadata = overrides.pop("metadata_json", {})
    if provider == "meta_whatsapp":
        metadata = {"encrypted_access_token": "encrypted", **metadata}
        overrides.setdefault("waba_id", "waba-1")
        overrides.setdefault("phone_number_id", "phone-1")
        overrides.setdefault("display_number", "+55 ***")
    return SimpleNamespace(
        id=overrides.pop("id", uuid.uuid4()),
        tipo=overrides.pop("tipo", "meta" if provider == "meta_whatsapp" else "evolution"),
        channel=overrides.pop("channel", "whatsapp"),
        provider=provider,
        status=status,
        nome_instancia=overrides.pop("nome_instancia", "Conexao"),
        metadata_json=metadata,
        credenciais=overrides.pop("credenciais", {}),
        atualizado_em=None,
        **overrides,
    )


class WhatsAppOperationalStatusTests(unittest.TestCase):
    def test_meta_connected_wins_over_evolution_disconnected(self):
        result = resolve_whatsapp_operational_status_from_connections(
            [
                _conn("evolution", "disconnected"),
                _conn("meta_whatsapp", "connected"),
            ]
        )

        self.assertEqual(result["status"], "connected")
        self.assertEqual(result["provider"], "meta_whatsapp")
        self.assertTrue(result["conectado"])

    def test_evolution_connected_is_legacy_when_meta_missing(self):
        result = resolve_whatsapp_operational_status_from_connections([_conn("evolution", "ativo")])

        self.assertEqual(result["status"], "connected")
        self.assertEqual(result["provider"], "evolution")
        self.assertIn("legada", result["warning"])

    def test_meta_connected_is_primary_when_both_are_connected(self):
        result = resolve_whatsapp_operational_status_from_connections(
            [
                _conn("evolution", "connected"),
                _conn("meta_whatsapp", "connected"),
            ]
        )

        self.assertEqual(result["provider"], "meta_whatsapp")
        self.assertEqual(result["reason"], "meta_connected")

    def test_meta_pending_is_not_reported_as_offline(self):
        result = resolve_whatsapp_operational_status_from_connections(
            [_conn("meta_whatsapp", "pending", phone_number_id=None)]
        )

        self.assertEqual(result["status"], "pending")
        self.assertEqual(result["provider"], "meta_whatsapp")
        self.assertFalse(result["conectado"])

    def test_without_connections_returns_not_configured(self):
        result = resolve_whatsapp_operational_status_from_connections([])

        self.assertEqual(result["status"], "not_configured")
        self.assertIsNone(result["provider"])


if __name__ == "__main__":
    unittest.main()

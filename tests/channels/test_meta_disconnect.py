import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.api.routers.conexoes import desconectar_conexao_meta
from db.models import TipoConexao


class FakeDb:
    def __init__(self):
        self.commits = 0

    async def commit(self):
        self.commits += 1


def connection():
    return SimpleNamespace(
        id="connection-1",
        tipo=TipoConexao.META,
        provider="meta_official",
        status="error",
        credenciais={"encrypted_access_token": "encrypted-secret", "token_last4": "oken"},
        external_account_id="old-phone",
        waba_id="old-waba",
        phone_number_id="phone-1",
        display_number="+55 11 99999-0000",
        nome_instancia="Rosangela Ferrioli",
        metadata_json={"verified_name": "Rosangela Ferrioli", "current_error": {"code": "invalid_object_id"}},
        channel="whatsapp",
        graph_api_version="v25.0",
    )


class MetaDisconnectTests(unittest.IsolatedAsyncioTestCase):
    async def test_disconnect_clears_local_connection_without_graph_call(self):
        db = FakeDb()
        conexao = connection()
        with patch("app.api.routers.conexoes._buscar_conexao_empresa", return_value=conexao), patch(
            "app.api.routers.conexoes.meta_settings", return_value=SimpleNamespace(live_actions_enabled=False)
        ), patch("app.api.routers.conexoes._audit_meta") as audit:
            result = await desconectar_conexao_meta("empresa-1", "connection-1", db=db, current_user=SimpleNamespace(id="user-1"))

        self.assertEqual(db.commits, 1)
        self.assertEqual(conexao.status, "disconnected")
        self.assertEqual(conexao.credenciais, {})
        self.assertIsNone(conexao.phone_number_id)
        self.assertIsNone(conexao.waba_id)
        self.assertIsNone(conexao.external_account_id)
        self.assertIsNone(conexao.display_number)
        self.assertEqual(conexao.metadata_json.keys(), {"disconnected_at"})
        self.assertFalse(result["configured"])
        self.assertIsNone(result["phone_number_id"])
        audit.assert_called_once()

    async def test_disconnect_is_idempotent_after_remote_object_is_gone(self):
        db = FakeDb()
        conexao = connection()
        conexao.status = "disconnected"
        conexao.credenciais = {}
        conexao.phone_number_id = None
        conexao.waba_id = None
        conexao.external_account_id = None
        conexao.display_number = None
        conexao.metadata_json = {"disconnected_at": "previous"}
        with patch("app.api.routers.conexoes._buscar_conexao_empresa", return_value=conexao), patch(
            "app.api.routers.conexoes.meta_settings", return_value=SimpleNamespace(live_actions_enabled=False)
        ), patch("app.api.routers.conexoes._audit_meta"):
            result = await desconectar_conexao_meta("empresa-1", "connection-1", db=db, current_user=SimpleNamespace(id="user-1"))

        self.assertEqual(db.commits, 1)
        self.assertEqual(result["status"], "disconnected")
        self.assertFalse(result["configured"])
        self.assertIsNone(result["waba_id"])
        self.assertIsNone(result["phone_number_id"])


import unittest
import uuid
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services import channel_factory
from app.services.channels.types import Provider
from db.models import TipoConexao


class _ScalarResult:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value


class _Result:
    def __init__(self, value):
        self.value = value

    def scalars(self):
        return _ScalarResult(self.value)


class _FakeSession:
    def __init__(self, connection):
        self.connection = connection

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def execute(self, *_args, **_kwargs):
        return _Result(self.connection)


class _FakeSessionFactory:
    def __init__(self, connection):
        self.connection = connection

    def __call__(self):
        return _FakeSession(self.connection)


class _Connection:
    def __init__(self, empresa_id):
        self.id = uuid.uuid4()
        self.empresa_id = empresa_id
        self.tipo = TipoConexao.META
        self.channel = "whatsapp"
        self.provider = Provider.META_WHATSAPP.value
        self.status = "ativo"
        self.external_account_id = "phone-1"
        self.phone_number_id = "phone-1"
        self.waba_id = "waba-1"
        self.metadata_json = {}
        self.credenciais = {}
        self.nome_instancia = "Meta Test"


class MetaDispatchBlockedTests(unittest.IsolatedAsyncioTestCase):
    async def test_leoft_meta_dispatch_ignores_controlled_test_allowlist(self):
        now = datetime(2026, 8, 18, 16, 0, 0)
        empresa_id = uuid.uuid4()
        connection = _Connection(empresa_id)
        empresa = SimpleNamespace(
            id=empresa_id,
            credenciais_canais={"ai_controlled_test": {"enabled": True, "status": "active"}},
        )

        allowed, reason = await channel_factory._meta_dispatch_allowed(
            session=_FakeSession(empresa),
            conexao=connection,
            empresa_id=str(empresa_id),
            destination="551188880000",
        )

        self.assertTrue(allowed)
        self.assertEqual(reason, "ai_gate_authorized")

    async def test_meta_without_transport_returns_false_not_success(self):
        empresa_id = uuid.uuid4()
        connection = _Connection(empresa_id)
        with patch.object(channel_factory, "AsyncSessionLocal", new=_FakeSessionFactory(connection)), patch.object(
            channel_factory.asyncio, "sleep", new=AsyncMock()
        ):
            result = await channel_factory.despachar_mensagem(
                canal="meta",
                identificador_origem="5511999999999",
                texto="oi",
                conexao_id=str(connection.id),
                empresa_id=str(empresa_id),
            )

        self.assertFalse(result)

    async def test_whatsapp_channel_with_meta_connection_returns_false_not_unknown_success(self):
        empresa_id = uuid.uuid4()
        connection = _Connection(empresa_id)
        with patch.object(channel_factory, "AsyncSessionLocal", new=_FakeSessionFactory(connection)), patch.object(
            channel_factory.asyncio, "sleep", new=AsyncMock()
        ):
            result = await channel_factory.despachar_mensagem(
                canal="whatsapp",
                identificador_origem="5511999999999",
                texto="oi",
                conexao_id=str(connection.id),
                empresa_id=str(empresa_id),
            )

        self.assertFalse(result)

    async def test_meta_dispatch_reaches_provider_without_legacy_test_gate(self):
        empresa_id = uuid.uuid4()
        connection = _Connection(empresa_id)
        with patch.object(channel_factory, "AsyncSessionLocal", new=_FakeSessionFactory(connection)), patch.object(
            channel_factory.asyncio, "sleep", new=AsyncMock()
        ), patch.object(
            channel_factory.MetaWhatsAppProvider, "send_text", new=AsyncMock(return_value={"messages": [{"id": "wamid.test"}]})
        ) as send_text:
            result = await channel_factory.despachar_mensagem(
                canal="meta",
                identificador_origem="5511999999999",
                texto="oi",
                conexao_id=str(connection.id),
                empresa_id=str(empresa_id),
            )

        self.assertTrue(result)
        send_text.assert_awaited_once()

    async def test_meta_dispatch_ignores_expired_legacy_test_mode(self):
        empresa_id = uuid.uuid4()
        connection = _Connection(empresa_id)
        empresa = SimpleNamespace(
            id=empresa_id,
            credenciais_canais={"ai_controlled_test": {"enabled": True, "expires_at": "2026-08-01T00:00:00"}},
        )

        allowed, reason = await channel_factory._meta_dispatch_allowed(
            session=_FakeSession(empresa),
            conexao=connection,
            empresa_id=str(empresa_id),
            destination="551188880000",
        )

        self.assertTrue(allowed)
        self.assertEqual(reason, "ai_gate_authorized")

    async def test_unknown_channel_returns_false_not_success(self):
        with patch.object(channel_factory.asyncio, "sleep", new=AsyncMock()):
            result = await channel_factory.despachar_mensagem(
                canal="whatsapp_desconhecido",
                identificador_origem="5511999999999",
                texto="oi",
                conexao_id=str(uuid.uuid4()),
                empresa_id=str(uuid.uuid4()),
            )

        self.assertFalse(result)

    async def test_meta_cross_tenant_connection_returns_false(self):
        empresa_id = uuid.uuid4()
        other_empresa = uuid.uuid4()
        connection = _Connection(other_empresa)
        with patch.object(channel_factory, "AsyncSessionLocal", new=_FakeSessionFactory(connection)), patch.object(
            channel_factory.asyncio, "sleep", new=AsyncMock()
        ):
            result = await channel_factory.despachar_mensagem(
                canal="meta",
                identificador_origem="5511999999999",
                texto="oi",
                conexao_id=str(connection.id),
                empresa_id=str(empresa_id),
            )

        self.assertFalse(result)

    async def test_evolution_legacy_dispatch_requires_explicit_gate(self):
        empresa_id = uuid.uuid4()
        with patch.dict("os.environ", {}, clear=True), patch.object(
            channel_factory.asyncio, "sleep", new=AsyncMock()
        ), patch.object(channel_factory, "_ensure_channel_registry") as ensure_registry:
            result = await channel_factory.despachar_mensagem(
                canal="evolution",
                identificador_origem="5511999999999",
                texto="oi",
                conexao_id=str(uuid.uuid4()),
                empresa_id=str(empresa_id),
            )

        self.assertFalse(result)
        ensure_registry.assert_not_called()

    def test_evolution_provider_has_no_bearer_auth_fallback(self):
        source = Path("app/services/mensageria/providers/evolution.py").read_text(encoding="utf-8")
        self.assertNotIn("Authorization", source)
        self.assertNotIn("Bearer", source)


if __name__ == "__main__":
    unittest.main()

import unittest
import importlib
import sys
import types
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.services.channels.contracts import (
    ChannelConnectionContext,
    ChannelOutboundMessage,
    ProviderCapabilities,
)
from app.services.channels.providers import EvolutionLegacyProvider
from app.services.channels.registry import ChannelProviderRegistry
from app.services.channels.resolver import (
    ChannelResolutionError,
    assert_message_matches_context,
    context_from_connection,
)
from app.services.channels.types import Channel, Provider


class FakeProvider:
    def __init__(self, context, connection=None):
        self.context = context
        self.connection = connection

    @property
    def provider_name(self):
        return self.context.provider

    @property
    def channel_name(self):
        return self.context.channel

    @property
    def capabilities(self):
        return ProviderCapabilities(supports_text=True)

    async def send_text(self, message):
        assert_message_matches_context(message, self.context)
        return {"provider": self.provider_name.value, "connection_id": self.context.connection_id}

    async def health_check(self):
        return {"ok": True}


class ChannelRegistryTests(unittest.IsolatedAsyncioTestCase):
    def _connection(self, *, empresa_id="empresa-a", id="wa-1", tipo="evolution", status="ativo"):
        return SimpleNamespace(
            empresa_id=empresa_id,
            id=id,
            tipo=tipo,
            status=status,
            nome_instancia=f"inst-{id}",
            credenciais={"evolution_instance": f"inst-{id}"},
        )

    def test_context_rejects_invalid_channel_provider(self):
        with self.assertRaises(ValueError):
            ChannelConnectionContext(
                empresa_id="empresa-a",
                connection_id="x",
                channel="sms",
                provider=Provider.EVOLUTION_LEGACY,
                status="ativo",
            )
        with self.assertRaises(ValueError):
            ChannelConnectionContext(
                empresa_id="empresa-a",
                connection_id="x",
                channel=Channel.WHATSAPP,
                provider="unknown",
                status="ativo",
            )

    def test_registry_resolves_by_connection_channel_provider(self):
        registry = ChannelProviderRegistry()
        registry.register(Channel.WHATSAPP, Provider.EVOLUTION_LEGACY, FakeProvider)
        context = context_from_connection(self._connection(id="wa-1"))
        adapter = registry.resolve(context, connection=self._connection(id="wa-1"))
        self.assertEqual(adapter.provider_name, Provider.EVOLUTION_LEGACY)
        self.assertEqual(adapter.channel_name, Channel.WHATSAPP)

    async def test_whatsapp_and_instagram_fake_coexist_without_cross_fallback(self):
        registry = ChannelProviderRegistry()
        registry.register(Channel.WHATSAPP, Provider.EVOLUTION_LEGACY, FakeProvider)
        registry.register(Channel.INSTAGRAM, Provider.META_INSTAGRAM, FakeProvider)
        wa_context = ChannelConnectionContext(
            empresa_id="empresa-a",
            connection_id="WA-1",
            channel=Channel.WHATSAPP,
            provider=Provider.EVOLUTION_LEGACY,
            status="ativo",
        )
        ig_context = ChannelConnectionContext(
            empresa_id="empresa-a",
            connection_id="IG-1",
            channel=Channel.INSTAGRAM,
            provider=Provider.META_INSTAGRAM,
            status="ativo",
        )
        wa_adapter = registry.resolve(wa_context)
        ig_adapter = registry.resolve(ig_context)

        wa_result = await wa_adapter.send_text(
            ChannelOutboundMessage(
                empresa_id="empresa-a",
                connection_id="WA-1",
                channel=Channel.WHATSAPP,
                provider=Provider.EVOLUTION_LEGACY,
                destination="5511999999999",
                text="oi",
            )
        )
        ig_result = await ig_adapter.send_text(
            ChannelOutboundMessage(
                empresa_id="empresa-a",
                connection_id="IG-1",
                channel=Channel.INSTAGRAM,
                provider=Provider.META_INSTAGRAM,
                destination="ig-user",
                text="oi",
            )
        )

        self.assertEqual(wa_result["connection_id"], "WA-1")
        self.assertEqual(ig_result["connection_id"], "IG-1")
        with self.assertRaises(ChannelResolutionError):
            await ig_adapter.send_text(
                ChannelOutboundMessage(
                    empresa_id="empresa-a",
                    connection_id="WA-1",
                    channel=Channel.WHATSAPP,
                    provider=Provider.EVOLUTION_LEGACY,
                    destination="5511999999999",
                    text="nao cruzar",
                )
            )

    def test_unknown_or_disabled_connection_fails_clearly(self):
        with self.assertRaises(ChannelResolutionError):
            context_from_connection(self._connection(status="desativado"))
        with self.assertRaises(ChannelResolutionError):
            context_from_connection(self._connection(tipo="sms"))

    async def test_evolution_legacy_provider_calls_existing_dispatcher(self):
        conexao = self._connection(id="wa-1")
        context = context_from_connection(conexao)
        provider = EvolutionLegacyProvider(context, conexao)
        with patch(
            "app.services.channels.providers.evolution_legacy._dispatch_outbound_message",
            new=AsyncMock(return_value={"key": {"id": "msg-1"}}),
        ) as dispatch:
            result = await provider.send_text(
                ChannelOutboundMessage(
                    empresa_id="empresa-a",
                    connection_id="wa-1",
                    channel=Channel.WHATSAPP,
                    provider=Provider.EVOLUTION_LEGACY,
                    destination="5511999999999",
                    text="mensagem",
                )
            )

        self.assertEqual(result, {"key": {"id": "msg-1"}})
        dispatch.assert_awaited_once()
        payload = dispatch.await_args.kwargs["payload"]
        self.assertEqual(payload.identificador_contato, "5511999999999")
        self.assertEqual(payload.canal, "whatsapp")
        self.assertEqual(payload.texto, "mensagem")
        self.assertEqual(payload.tipo, "text")

    async def test_evolution_legacy_rejects_cross_tenant_message(self):
        conexao = self._connection(id="wa-1", empresa_id="empresa-a")
        context = context_from_connection(conexao)
        provider = EvolutionLegacyProvider(context, conexao)
        with self.assertRaises(ChannelResolutionError):
            await provider.send_text(
                ChannelOutboundMessage(
                    empresa_id="empresa-b",
                    connection_id="wa-1",
                    channel=Channel.WHATSAPP,
                    provider=Provider.EVOLUTION_LEGACY,
                    destination="5511999999999",
                    text="bloqueado",
                )
            )

    async def test_channel_factory_explicit_connection_does_not_fallback_to_other_company_connection(self):
        class FakeSession:
            def __init__(self):
                self.execute = AsyncMock(side_effect=[SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None))])

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb):
                return False

        class FakeSessionFactory:
            def __call__(self):
                return FakeSession()

        stub_db_database = types.ModuleType("db.database")
        stub_db_database.AsyncSessionLocal = FakeSessionFactory
        stub_db_models = types.ModuleType("db.models")

        class StubConexao:
            id = object()
            empresa_id = object()
            tipo = object()
            status = object()
            nome_instancia = "stub"
            credenciais = {}

        class StubTipoConexao:
            EVOLUTION = "evolution"

        stub_db_models.Conexao = StubConexao
        stub_db_models.TipoConexao = StubTipoConexao

        stub_sqlalchemy = types.ModuleType("sqlalchemy")

        class StubSelect:
            def where(self, *args, **kwargs):
                return self

        stub_sqlalchemy.String = object
        stub_sqlalchemy.cast = lambda *args, **kwargs: None
        stub_sqlalchemy.func = types.SimpleNamespace(lower=lambda value: value, in_=lambda value: value)
        stub_sqlalchemy.select = lambda *args, **kwargs: StubSelect()

        stub_pkg = types.ModuleType("app.agents.atendimento")
        stub_segmenter = types.ModuleType("app.agents.atendimento.whatsapp_segmenter")
        stub_segmenter.segment_whatsapp_response = lambda texto: [texto]
        stub_pkg.whatsapp_segmenter = stub_segmenter

        with patch.dict(
            sys.modules,
            {
                "db.database": stub_db_database,
                "db.models": stub_db_models,
                "sqlalchemy": stub_sqlalchemy,
                "app.agents.atendimento": stub_pkg,
                "app.agents.atendimento.whatsapp_segmenter": stub_segmenter,
            },
        ):
            channel_factory_module = importlib.import_module("app.services.channel_factory")

            with patch.object(channel_factory_module, "AsyncSessionLocal", new=FakeSessionFactory()), patch.object(
                channel_factory_module.asyncio, "sleep", new=AsyncMock()
            ):
                result = await channel_factory_module.despachar_mensagem(
                    canal="evolution",
                    identificador_origem="5511999999999",
                    texto="oi",
                    conexao_id="conexao-inexistente",
                    empresa_id="empresa-a",
                )

        self.assertFalse(result)

    def _import_evolution_service_with_stubs(self):
        class StubColumn:
            def __init__(self, name):
                self.name = name

            def __eq__(self, other):
                return (self.name, other)

        class StubSelect:
            def where(self, *args, **kwargs):
                return self

        stub_sqlalchemy = types.ModuleType("sqlalchemy")
        stub_sqlalchemy.select = lambda *args, **kwargs: StubSelect()

        stub_sqlalchemy_ext = types.ModuleType("sqlalchemy.ext")
        stub_sqlalchemy_ext_asyncio = types.ModuleType("sqlalchemy.ext.asyncio")
        stub_sqlalchemy_ext_asyncio.AsyncSession = object

        stub_db_database = types.ModuleType("db.database")
        stub_db_database.AsyncSessionLocal = object

        stub_db_models = types.ModuleType("db.models")

        class StubEmpresa:
            id = StubColumn("empresa_id")

            def __init__(self, credenciais_canais=None):
                self.credenciais_canais = credenciais_canais

        class StubConexao:
            id = StubColumn("conexao_id")
            empresa_id = StubColumn("empresa_id")
            tipo = StubColumn("tipo")

            def __init__(self, *, id, empresa_id, tipo="evolution", credenciais=None, status="ativo"):
                self.id = id
                self.empresa_id = empresa_id
                self.tipo = tipo
                self.credenciais = credenciais
                self.status = status
                self.nome_instancia = "inst-test"

        class StubTipoConexao:
            EVOLUTION = types.SimpleNamespace(value="evolution")
            META = types.SimpleNamespace(value="meta")
            INSTAGRAM = types.SimpleNamespace(value="instagram")

        stub_db_models.Empresa = StubEmpresa
        stub_db_models.Conexao = StubConexao
        stub_db_models.TipoConexao = StubTipoConexao

        stub_httpx = types.ModuleType("httpx")
        stub_httpx.AsyncClient = object
        stub_httpx.Response = object
        stub_httpx.Timeout = lambda *args, **kwargs: object()

        with patch.dict(
            sys.modules,
            {
                "sqlalchemy": stub_sqlalchemy,
                "sqlalchemy.ext": stub_sqlalchemy_ext,
                "sqlalchemy.ext.asyncio": stub_sqlalchemy_ext_asyncio,
                "db.database": stub_db_database,
                "db.models": stub_db_models,
                "httpx": stub_httpx,
            },
        ):
            sys.modules.pop("app.services.evolution_service", None)
            module = importlib.import_module("app.services.evolution_service")
        self.addCleanup(sys.modules.pop, "app.services.evolution_service", None)
        return module, StubConexao, StubEmpresa

    def _result_with_first(self, value):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: value))

    async def test_evolution_credentials_explicit_connection_same_company_allowed(self):
        module, StubConexao, _ = self._import_evolution_service_with_stubs()
        empresa_id = uuid.uuid4()
        conexao_id = uuid.uuid4()
        db = SimpleNamespace(
            execute=AsyncMock(
                return_value=self._result_with_first(
                    StubConexao(
                        id=conexao_id,
                        empresa_id=empresa_id,
                        credenciais={
                            "evolution_apikey": "secret-key-a",
                            "evolution_instance": "inst-a",
                        },
                    )
                )
            )
        )

        with patch.object(module, "evolution_base_url", return_value="https://fallback.local"):
            result = await module._obter_credenciais_evolution(
                empresa_id,
                db,
                conexao_id=conexao_id,
            )

        self.assertEqual(result["evolution_apikey"], "secret-key-a")
        self.assertEqual(result["evolution_instance"], "inst-a")
        self.assertEqual(result["evolution_url"], "https://fallback.local")
        db.execute.assert_awaited_once()

    async def test_evolution_credentials_explicit_connection_other_company_blocks_without_fallback(self):
        module, StubConexao, _ = self._import_evolution_service_with_stubs()
        empresa_id = uuid.uuid4()
        db = SimpleNamespace(
            execute=AsyncMock(
                return_value=self._result_with_first(
                    StubConexao(
                        id=uuid.uuid4(),
                        empresa_id=uuid.uuid4(),
                        credenciais={
                            "evolution_apikey": "secret-cross-tenant",
                            "evolution_instance": "inst-cross",
                        },
                    )
                )
            )
        )

        with patch("builtins.print") as print_mock:
            with self.assertRaises(module.EvolutionCredentialResolutionError) as ctx:
                await module._obter_credenciais_evolution(
                    empresa_id,
                    db,
                    conexao_id=uuid.uuid4(),
                )

        self.assertNotIn("secret-cross-tenant", str(ctx.exception))
        self.assertEqual(db.execute.await_count, 1)
        printed = " ".join(str(call) for call in print_mock.call_args_list)
        self.assertNotIn("secret-cross-tenant", printed)
        self.assertNotIn("inst-cross", printed)

    async def test_evolution_credentials_explicit_missing_connection_blocks_without_fallback(self):
        module, _, _ = self._import_evolution_service_with_stubs()
        db = SimpleNamespace(execute=AsyncMock(return_value=self._result_with_first(None)))

        with self.assertRaises(module.EvolutionCredentialResolutionError):
            await module._obter_credenciais_evolution(
                uuid.uuid4(),
                db,
                conexao_id=uuid.uuid4(),
            )

        self.assertEqual(db.execute.await_count, 1)

    async def test_evolution_credentials_explicit_incompatible_provider_blocks_without_fallback(self):
        module, StubConexao, _ = self._import_evolution_service_with_stubs()
        empresa_id = uuid.uuid4()
        db = SimpleNamespace(
            execute=AsyncMock(
                return_value=self._result_with_first(
                    StubConexao(
                        id=uuid.uuid4(),
                        empresa_id=empresa_id,
                        tipo="instagram",
                        credenciais={
                            "evolution_apikey": "secret-provider",
                            "evolution_instance": "inst-provider",
                        },
                    )
                )
            )
        )

        with self.assertRaises(module.EvolutionCredentialResolutionError):
            await module._obter_credenciais_evolution(
                empresa_id,
                db,
                conexao_id=uuid.uuid4(),
            )

        self.assertEqual(db.execute.await_count, 1)

    async def test_evolution_credentials_explicit_connection_requires_empresa_id(self):
        module, _, _ = self._import_evolution_service_with_stubs()
        db = SimpleNamespace(execute=AsyncMock())

        with self.assertRaises(module.EvolutionCredentialResolutionError):
            await module._obter_credenciais_evolution(
                None,
                db,
                conexao_id=uuid.uuid4(),
            )

        db.execute.assert_not_awaited()

    async def test_evolution_credentials_explicit_failure_does_not_select_default_connection(self):
        module, _, StubEmpresa = self._import_evolution_service_with_stubs()
        db = SimpleNamespace(
            execute=AsyncMock(
                side_effect=[
                    self._result_with_first(None),
                    self._result_with_first(
                        StubEmpresa(
                            {
                                "evolution_apikey": "secret-default",
                                "evolution_instance": "inst-default",
                            }
                        )
                    ),
                ]
            )
        )

        with self.assertRaises(module.EvolutionCredentialResolutionError):
            await module._obter_credenciais_evolution(
                uuid.uuid4(),
                db,
                conexao_id=uuid.uuid4(),
            )

        self.assertEqual(db.execute.await_count, 1)

    async def test_evolution_credentials_without_explicit_connection_preserves_company_fallback(self):
        module, _, StubEmpresa = self._import_evolution_service_with_stubs()
        db = SimpleNamespace(
            execute=AsyncMock(
                return_value=self._result_with_first(
                    StubEmpresa(
                        {
                            "evolution_apikey": "secret-company",
                            "evolution_instance": "inst-company",
                        }
                    )
                )
            )
        )

        with patch.object(module, "evolution_base_url", return_value="https://legacy.local"):
            result = await module._obter_credenciais_evolution(
                uuid.uuid4(),
                db,
                conexao_id=None,
            )

        self.assertEqual(result["evolution_apikey"], "secret-company")
        self.assertEqual(result["evolution_instance"], "inst-company")
        self.assertEqual(result["evolution_url"], "https://legacy.local")
        db.execute.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()

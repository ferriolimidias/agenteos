import unittest
from unittest.mock import patch

from app.schemas import StandardMessage
from app.services.channels.resolver import ChannelResolutionError
from app.services.channels.routing import (
    CanonicalMessageRoute,
    conversation_runtime_key,
    dispatch_canal_for_route,
    message_requires_canonical_route,
    resolve_outbound_route,
    route_from_standard_message,
    runtime_key_from_standard_message,
)
from app.services.channels.types import Channel, Provider


class MessageRoutingTests(unittest.TestCase):
    def _message(self, **overrides):
        data = {
            "empresa_id": "empresa-a",
            "canal": "evolution",
            "identificador_origem": "5511999999999",
            "conexao_id": "wa-1",
            "texto_mensagem": "oi",
            "is_human_agent": False,
            "external_message_id": "msg-1",
            "external_contact_id": "contact-1",
            "external_thread_id": "thread-1",
            "conversation_key": "conv-1",
        }
        data.update(overrides)
        return StandardMessage(**data)

    def test_standard_message_builds_canonical_evolution_route(self):
        route = route_from_standard_message(self._message())

        self.assertEqual(route.empresa_id, "empresa-a")
        self.assertEqual(route.connection_id, "wa-1")
        self.assertEqual(route.channel, Channel.WHATSAPP)
        self.assertEqual(route.provider, Provider.EVOLUTION_LEGACY)
        self.assertEqual(route.destination, "5511999999999")
        self.assertEqual(route.external_message_id, "msg-1")
        self.assertEqual(dispatch_canal_for_route(route), "evolution")

    def test_meta_whatsapp_dispatch_uses_provider_not_channel(self):
        route = route_from_standard_message(
            self._message(canal="whatsapp", provider="meta_whatsapp", conexao_id="meta-1")
        )

        self.assertEqual(route.channel, Channel.WHATSAPP)
        self.assertEqual(route.provider, Provider.META_WHATSAPP)
        self.assertEqual(dispatch_canal_for_route(route), "meta")

    def test_route_rejects_inconsistent_channel_provider(self):
        message = self._message(canal="instagram", provider="evolution_legacy")

        with self.assertRaises(ChannelResolutionError):
            route_from_standard_message(message)

    def test_outbound_route_rejects_connection_swap_when_origin_is_explicit(self):
        route = route_from_standard_message(self._message())

        with self.assertRaises(ChannelResolutionError):
            resolve_outbound_route(route, {"conexao_id": "wa-2"})

    def test_outbound_route_rejects_provider_swap(self):
        route = route_from_standard_message(self._message())

        with self.assertRaises(ChannelResolutionError):
            resolve_outbound_route(route, {"provider": "meta_whatsapp"})

    def test_outbound_route_rejects_channel_swap(self):
        route = route_from_standard_message(self._message())

        with self.assertRaises(ChannelResolutionError):
            resolve_outbound_route(route, {"channel": "instagram"})

    def test_outbound_route_accepts_same_connection_aliases(self):
        route = route_from_standard_message(self._message())
        resolved = resolve_outbound_route(
            route,
            {
                "connection_id": "wa-1",
                "channel": "whatsapp",
                "provider": "evolution_legacy",
                "empresa_id": "empresa-a",
            },
        )

        self.assertEqual(resolved, route)

    def test_runtime_key_includes_provider_and_connection(self):
        route = route_from_standard_message(self._message())
        key = conversation_runtime_key(route)

        self.assertIn("empresa-a", key)
        self.assertIn("whatsapp", key)
        self.assertIn("evolution_legacy", key)
        self.assertIn("wa-1", key)
        self.assertIn("conv-1", key)

    def test_runtime_key_distinguishes_two_connections_same_contact(self):
        first = runtime_key_from_standard_message(self._message(conexao_id="wa-1"))
        second = runtime_key_from_standard_message(self._message(conexao_id="wa-2"))

        self.assertNotEqual(first, second)

    def test_legacy_without_connection_can_adopt_graph_connection_once(self):
        route = CanonicalMessageRoute(
            empresa_id="empresa-a",
            channel=Channel.WHATSAPP,
            provider=Provider.EVOLUTION_LEGACY,
            connection_id=None,
            destination="5511999999999",
        )

        resolved = resolve_outbound_route(route, {"conexao_id": "wa-default"})

        self.assertEqual(resolved.connection_id, "wa-default")
        self.assertEqual(resolved.provider, Provider.EVOLUTION_LEGACY)

    def test_invalid_route_fallback_key_preserves_provider_and_connection(self):
        message = self._message(canal="instagram", provider="evolution_legacy", conexao_id="ig-1")

        key = runtime_key_from_standard_message(message)

        self.assertIn("evolution_legacy", key)
        self.assertIn("ig-1", key)

    def test_only_unscoped_legacy_channel_may_skip_canonical_route(self):
        legacy = self._message(canal="simulador", conexao_id=None, provider=None, channel=None)
        explicit = self._message(canal="simulador", conexao_id="conn-1")

        self.assertFalse(message_requires_canonical_route(legacy))
        self.assertTrue(message_requires_canonical_route(explicit))


class ProcessMessageBlockRoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_invalid_explicit_route_aborts_before_graph_and_dispatch(self):
        from app.api.utils import processar_bloco_mensagens

        message = StandardMessage(
            empresa_id="empresa-a",
            canal="instagram",
            provider="evolution_legacy",
            identificador_origem="ig-user",
            conexao_id="ig-1",
            texto_mensagem="oi",
            is_human_agent=False,
        )

        with patch("app.api.utils.logger.warning") as warning:
            result = await processar_bloco_mensagens([message])

        self.assertIsNone(result)
        warning.assert_called_once()
        self.assertIn("bloqueada", warning.call_args.args[0])


if __name__ == "__main__":
    unittest.main()

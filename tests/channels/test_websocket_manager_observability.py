import unittest

from app.services.websocket_manager import ConnectionManager


class FakeSocket:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.sent = []

    async def accept(self, subprotocol=None):
        return None

    async def send_text(self, payload):
        if self.fail:
            raise RuntimeError("simulated websocket failure")
        self.sent.append(payload)


class WebSocketManagerObservabilityTests(unittest.IsolatedAsyncioTestCase):
    async def test_registered_socket_receives_broadcast(self):
        manager = ConnectionManager()
        socket = FakeSocket()
        await manager.connect("empresa-a", socket)

        await manager.broadcast_to_empresa("empresa-a", {"type": "inbox.message.created"})

        self.assertEqual(len(socket.sent), 1)

    async def test_empty_empresa_has_no_send_target(self):
        manager = ConnectionManager()

        await manager.broadcast_to_empresa("empresa-a", {"type": "inbox.message.created"})

        self.assertEqual(manager._connections_by_empresa.get("empresa-a", []), [])

    async def test_failed_socket_is_removed(self):
        manager = ConnectionManager()
        socket = FakeSocket(fail=True)
        await manager.connect("empresa-a", socket)

        await manager.broadcast_to_empresa("empresa-a", {"type": "inbox.message.created"})

        self.assertEqual(manager._connections_by_empresa.get("empresa-a", []), [])

    async def test_broadcast_is_tenant_scoped(self):
        manager = ConnectionManager()
        socket_a = FakeSocket()
        socket_b = FakeSocket()
        await manager.connect("empresa-a", socket_a)
        await manager.connect("empresa-b", socket_b)

        await manager.broadcast_to_empresa("empresa-a", {"type": "inbox.message.created"})

        self.assertEqual(len(socket_a.sent), 1)
        self.assertEqual(socket_b.sent, [])


if __name__ == "__main__":
    unittest.main()

from collections import defaultdict
import json

from fastapi import WebSocket


class ConnectionManager:
    def __init__(self) -> None:
        self._connections_by_empresa: dict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, empresa_id: str, websocket: WebSocket) -> None:
        await websocket.accept()
        self._connections_by_empresa[empresa_id].append(websocket)

    def disconnect(self, empresa_id: str, websocket: WebSocket) -> None:
        lista = self._connections_by_empresa.get(empresa_id, [])
        if websocket in lista:
            lista.remove(websocket)
        if not lista and empresa_id in self._connections_by_empresa:
            self._connections_by_empresa.pop(empresa_id, None)

    async def broadcast_to_empresa(self, empresa_id: str, message_data: dict) -> None:
        conexoes = list(self._connections_by_empresa.get(empresa_id, []))
        if not conexoes:
            return

        serialized = json.dumps(message_data, ensure_ascii=False)
        stale: list[WebSocket] = []
        for ws in conexoes:
            try:
                await ws.send_text(serialized)
            except Exception:
                stale.append(ws)

        for ws in stale:
            self.disconnect(empresa_id, ws)


manager = ConnectionManager()

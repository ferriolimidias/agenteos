from collections import defaultdict
import json
import logging

from fastapi import WebSocket


logger = logging.getLogger(__name__)


class ConnectionManager:
    def __init__(self) -> None:
        self._connections_by_empresa: dict[str, list[WebSocket]] = defaultdict(list)

    async def connect(self, empresa_id: str, websocket: WebSocket, *, subprotocol: str | None = None) -> None:
        await websocket.accept(subprotocol=subprotocol)
        self._connections_by_empresa[empresa_id].append(websocket)
        logger.warning(
            "[WS_CONNECT] empresa_id=%s connections_for_empresa=%s",
            empresa_id,
            len(self._connections_by_empresa[empresa_id]),
        )

    def disconnect(self, empresa_id: str, websocket: WebSocket) -> None:
        lista = self._connections_by_empresa.get(empresa_id, [])
        if websocket in lista:
            lista.remove(websocket)
        if not lista and empresa_id in self._connections_by_empresa:
            self._connections_by_empresa.pop(empresa_id, None)
        logger.warning(
            "[WS_DISCONNECT] empresa_id=%s connections_remaining=%s",
            empresa_id,
            len(self._connections_by_empresa.get(empresa_id, [])),
        )

    async def broadcast_to_empresa(self, empresa_id: str, message_data: dict) -> int:
        conexoes = list(self._connections_by_empresa.get(empresa_id, []))
        event_type = str(message_data.get("type") or message_data.get("tipo_evento") or "unknown")
        logger.warning(
            "[WS_BROADCAST] empresa_id=%s event_type=%s target_count=%s",
            empresa_id,
            event_type,
            len(conexoes),
        )
        if not conexoes:
            return 0

        serialized = json.dumps(message_data, ensure_ascii=False)
        stale: list[WebSocket] = []
        for socket_index, ws in enumerate(conexoes):
            logger.warning(
                "[WS_SEND] empresa_id=%s event_type=%s socket_index=%s attempted=true",
                empresa_id,
                event_type,
                socket_index,
            )
            try:
                await ws.send_text(serialized)
                logger.warning(
                    "[WS_SEND] empresa_id=%s event_type=%s socket_index=%s success=true",
                    empresa_id,
                    event_type,
                    socket_index,
                )
            except Exception as exc:
                logger.warning(
                    "[WS_SEND] empresa_id=%s event_type=%s socket_index=%s success=false error_class=%s",
                    empresa_id,
                    event_type,
                    socket_index,
                    exc.__class__.__name__,
                )
                stale.append(ws)

        for ws in stale:
            self.disconnect(empresa_id, ws)
        logger.warning(
            "[WS_BROADCAST] empresa_id=%s event_type=%s target_count=%s delivered_count=%s stale_count=%s",
            empresa_id,
            event_type,
            len(conexoes),
            len(conexoes) - len(stale),
            len(stale),
        )
        return len(conexoes)


manager = ConnectionManager()

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.websocket_manager import manager


router = APIRouter()


@router.websocket("/{empresa_id}/ws")
async def websocket_empresa(websocket: WebSocket, empresa_id: str):
    await manager.connect(empresa_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        manager.disconnect(empresa_id, websocket)
    except Exception:
        manager.disconnect(empresa_id, websocket)

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from app.services.websocket_manager import manager


router = APIRouter(tags=["WebSockets"])


@router.get("/{empresa_id}/ws")
async def debug_ws_fallback(empresa_id: str):
    return {
        "erro": "O proxy reverso (Nginx/Cloudflare) converteu o WebSocket em GET. Configure os headers Upgrade e Connection no proxy.",
        "empresa_id": empresa_id,
    }


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

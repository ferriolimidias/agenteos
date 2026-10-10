import uuid
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, status
from sqlalchemy import select

from app.core.security import decode_access_token
from app.api.routers.auth import (
    is_admin_empresa_role,
    is_meta_app_reviewer_role,
    is_root_admin_email,
    is_super_admin_role,
)
from db.database import AsyncSessionLocal
from db.models import Usuario
from app.services.websocket_manager import manager


router = APIRouter()
logger = logging.getLogger(__name__)


async def _authorize_websocket(websocket: WebSocket, empresa_id: str) -> bool:
    subprotocols = websocket.scope.get("subprotocols") or []
    token = str(subprotocols[0] if subprotocols else "").strip()
    try:
        claims = decode_access_token(token)
        user_id = uuid.UUID(str(claims.get("sub")))
        tenant_id = uuid.UUID(str(empresa_id))
    except (ValueError, TypeError):
        return False

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Usuario).where(Usuario.id == user_id, Usuario.ativo.is_(True)))
        user = result.scalars().first()
    if not user:
        return False
    if is_root_admin_email(user.email) or is_super_admin_role(user.role):
        return True
    if is_admin_empresa_role(user.role) and user.empresa_id == tenant_id:
        return True
    return bool(is_meta_app_reviewer_role(user.role) and user.empresa_id == tenant_id)


@router.websocket("/empresas/{empresa_id}/ws")
async def websocket_empresa(websocket: WebSocket, empresa_id: str):
    if not await _authorize_websocket(websocket, empresa_id):
        logger.warning("inbox.realtime.auth_rejected empresa_id=%s", empresa_id)
        await websocket.close(code=status.WS_1008_POLICY_VIOLATION)
        return
    logger.info("Inbox WebSocket autenticado e inscrito empresa_id=%s", empresa_id)
    await manager.connect(empresa_id, websocket, subprotocol="inbox.v1")
    logger.warning("[WS_ROUTE_CONNECTED] empresa_id=%s manager_registered=true", empresa_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect as exc:
        logger.info("Inbox WebSocket encerrado empresa_id=%s code=%s", empresa_id, exc.code)
        manager.disconnect(empresa_id, websocket)
        logger.warning("[WS_ROUTE_DISCONNECTED] empresa_id=%s", empresa_id)
    except Exception as exc:
        logger.warning("Inbox WebSocket falhou empresa_id=%s erro=%s", empresa_id, str(exc)[:200])
        manager.disconnect(empresa_id, websocket)
        logger.warning("[WS_ROUTE_DISCONNECTED] empresa_id=%s", empresa_id)

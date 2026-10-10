from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
import uuid

from db.database import AsyncSessionLocal
from db.models import Usuario, WebhookSaida
from sqlalchemy import select
from app.api.routers.auth import require_super_admin, require_tenant_access

router = APIRouter(prefix="/api/empresas", tags=["Integrações"])

class WebhookPayload(BaseModel):
    url: str
    ativo: bool = True

@router.get("/{empresa_id}/webhooks")
async def listar_webhook(empresa_id: str, _: Usuario = Depends(require_tenant_access)):
    try:
        empresa_uuid = uuid.UUID(empresa_id)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(WebhookSaida).where(WebhookSaida.empresa_id == empresa_uuid)
            )
            webhook = result.scalars().first()
            
            if webhook:
                return {
                    "id": str(webhook.id),
                    "url": webhook.url,
                    "ativo": webhook.ativo
                }
            return {}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.post("/{empresa_id}/webhooks")
async def salvar_webhook(
    empresa_id: str,
    payload: WebhookPayload,
    _: Usuario = Depends(require_super_admin),
):
    try:
        empresa_uuid = uuid.UUID(empresa_id)
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(WebhookSaida).where(WebhookSaida.empresa_id == empresa_uuid)
            )
            webhook = result.scalars().first()
            
            if webhook:
                webhook.url = payload.url
                webhook.ativo = payload.ativo
            else:
                novo_webhook = WebhookSaida(
                    empresa_id=empresa_uuid,
                    url=payload.url,
                    ativo=payload.ativo
                )
                session.add(novo_webhook)
                
            await session.commit()
            return {"status": "success", "message": "Webhook atualizado com sucesso"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

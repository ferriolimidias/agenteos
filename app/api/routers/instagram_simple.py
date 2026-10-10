from __future__ import annotations

import uuid
import logging
from typing import Any, Literal
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.auth import require_tenant_access
from app.services.instagram_simple import (
    InstagramGraphClient,
    InstagramGraphError,
    connection_token,
    get_connection,
    identify_connection,
    persist_connection,
    reconfigure_connection,
    revalidate_connection,
    serialize_connection,
)
from db.database import get_db
from db.models import Conexao, InstagramCommentAutomation, InstagramCommentExecution, TipoConexao, Usuario

router = APIRouter(prefix="/api/empresas/{empresa_id}/instagram", tags=["Instagram"])
logger = logging.getLogger(__name__)


class InstagramConnectionCredentialsInput(BaseModel):
    instagram_id: str | None = Field(default=None, min_length=1, max_length=120)
    instagram_professional_account_id: str | None = Field(default=None, min_length=1, max_length=120)
    instagram_user_access_token: str = Field(min_length=8, max_length=4096)

    @model_validator(mode="after")
    def _validate_instagram_id(self) -> "InstagramConnectionCredentialsInput":
        if not self.instagram_id and not self.instagram_professional_account_id:
            raise ValueError("Instagram ID é obrigatório.")
        return self

    @property
    def account_id(self) -> str:
        return str(self.instagram_id or self.instagram_professional_account_id or "").strip()


class SaveConnectionInput(InstagramConnectionCredentialsInput):
    name: str | None = Field(default=None, max_length=160)


class PrivateReplyButtonInput(BaseModel):
    type: Literal["web_url", "postback"]
    title: str = Field(min_length=1, max_length=80)
    url: str | None = Field(default=None, max_length=2000)
    payload: str | None = Field(default=None, max_length=1000)

    @field_validator("title", "url", "payload", mode="before")
    @classmethod
    def _strip_strings(cls, value: Any) -> Any:
        return value.strip() if isinstance(value, str) else value

    @model_validator(mode="after")
    def _validate_button(self) -> "PrivateReplyButtonInput":
        if self.type == "web_url":
            if not self.url:
                raise ValueError("URL do botão é obrigatória.")
            parsed = urlparse(self.url)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("URL do botão deve começar com http:// ou https://.")
        if self.type == "postback" and not self.payload:
            raise ValueError("Payload do botão postback é obrigatório.")
        return self


class PrivateReplyConfigInput(BaseModel):
    message_type: Literal["text", "button_template"] = "text"
    text: str = Field(default="", max_length=2000)
    buttons: list[PrivateReplyButtonInput] = Field(default_factory=list, max_length=3)

    @field_validator("text", mode="before")
    @classmethod
    def _strip_text(cls, value: Any) -> str:
        return str(value or "").strip()

    @model_validator(mode="after")
    def _validate_config(self) -> "PrivateReplyConfigInput":
        if not self.text:
            raise ValueError("Texto da mensagem privada é obrigatório.")
        if self.message_type == "text":
            self.buttons = []
            return self
        if not self.buttons:
            raise ValueError("Adicione ao menos um botão.")
        if len(self.buttons) > 3:
            raise ValueError("A mensagem privada permite no máximo 3 botões.")
        return self

    def sanitized(self) -> dict[str, Any]:
        return {
            "message_type": self.message_type,
            "text": self.text,
            "buttons": [
                {
                    "type": button.type,
                    "title": button.title,
                    **({"url": button.url} if button.type == "web_url" else {}),
                    **({"payload": button.payload} if button.type == "postback" else {}),
                }
                for button in self.buttons[:3]
            ],
        }


class AutomationInput(BaseModel):
    connection_id: uuid.UUID
    name: str = Field(min_length=1, max_length=160)
    active: bool = True
    media_id: str | None = Field(default=None, max_length=120)
    keywords: list[str] = Field(default_factory=list)
    public_reply_enabled: bool = True
    fixed_reply: str | None = Field(default=None, max_length=1000)
    private_reply_enabled: bool = False
    private_message: str | None = Field(default=None, max_length=2000)
    private_link: str | None = Field(default=None, max_length=2000)
    private_reply_config: PrivateReplyConfigInput | None = None

    @model_validator(mode="after")
    def _validate_private_reply(self) -> "AutomationInput":
        if not self.private_reply_enabled:
            return self
        if self.private_reply_config:
            return self
        if not str(self.private_message or "").strip():
            raise ValueError("Mensagem privada é obrigatória.")
        return self


def _private_reply_config_payload(payload: AutomationInput) -> dict[str, Any] | None:
    if not payload.private_reply_enabled:
        return None
    if payload.private_reply_config:
        return payload.private_reply_config.sanitized()
    private_text = str(payload.private_message or "").strip()
    if not private_text:
        return None
    return {"message_type": "text", "text": private_text, "buttons": []}


def _automation_private_reply_config(row: InstagramCommentAutomation) -> dict[str, Any] | None:
    if isinstance(row.private_reply_config, dict):
        return row.private_reply_config
    if row.private_message:
        return {"message_type": "text", "text": row.private_message, "buttons": []}
    return None


def _empresa_id(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except ValueError as exc:
        raise HTTPException(400, "empresa_id inválido.") from exc


def _graph_error(exc: Exception) -> HTTPException:
    if isinstance(exc, InstagramGraphError):
        code = exc.code
        if code == 190:
            message = "Token inválido ou expirado — código 190."
        elif code is not None:
            message = f"A Meta rejeitou a solicitação — código {code}."
        elif exc.meta_message:
            message = exc.meta_message
        else:
            message = "A conta profissional informada não pôde ser consultada."
        return HTTPException(422, {"code": "INSTAGRAM_GRAPH_VALIDATION_FAILED", "meta_code": code, "message": message})
    if isinstance(exc, ValueError):
        duplicate = "já possui" in str(exc)
        detail = str(exc)
        if "ID informado" in detail or "ID ou token" in detail:
            message = "O ID informado não corresponde à conta acessível pelo token."
        elif "permiss" in detail.lower():
            message = "O token não possui as permissões necessárias."
        elif "expir" in detail.lower() or "obrigatório" in detail.lower():
            message = detail
        else:
            message = "A conexão Instagram informada é inválida ou já está cadastrada." if duplicate else "A conexão Instagram informada é inválida."
        return HTTPException(409 if duplicate else 422, {"code": "INSTAGRAM_CONNECTION_INVALID", "message": message})
    return HTTPException(502, {"code": "INSTAGRAM_SERVICE_UNAVAILABLE", "message": "Não foi possível concluir a operação com a Meta."})


def _automation_payload(row: InstagramCommentAutomation) -> dict[str, Any]:
    return {
        "id": str(row.id), "connection_id": str(row.connection_id) if row.connection_id else None,
        "name": row.name, "active": row.active, "media_id": row.media_id,
        "keywords": list(row.keywords or []), "public_reply_enabled": row.public_reply_enabled,
        "fixed_reply": row.fixed_reply, "private_reply_enabled": row.private_reply_enabled,
        "private_message": row.private_message, "private_link": row.private_link,
        "private_reply_config": _automation_private_reply_config(row),
    }


@router.get("/connections")
async def list_connections(
    empresa_id: str, db: AsyncSession = Depends(get_db), _: Usuario = Depends(require_tenant_access),
):
    empresa = _empresa_id(empresa_id)
    rows = (await db.execute(select(Conexao).where(
        Conexao.empresa_id == empresa, Conexao.tipo == TipoConexao.INSTAGRAM,
        Conexao.channel == "instagram", Conexao.provider == "meta_instagram",
    ).order_by(Conexao.criado_em.asc()))).scalars().all()
    return {"success": True, "connections": [serialize_connection(row) for row in rows]}


@router.post("/connection", status_code=201)
async def save_connection(
    empresa_id: str, payload: SaveConnectionInput, db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    tenant_id = _empresa_id(empresa_id)
    try:
        identification = await identify_connection(
            professional_account_id=payload.account_id,
            access_token=payload.instagram_user_access_token,
        )
        connection = await persist_connection(
            db, empresa_id=tenant_id,
            professional_account_id=payload.account_id,
            access_token=payload.instagram_user_access_token,
            identified=identification,
            name=payload.name,
        )
    except Exception as exc:
        await db.rollback()
        raise _graph_error(exc) from exc
    return {"success": True, "connection": serialize_connection(connection)}


@router.post("/connection/{connection_id}/revalidate")
async def revalidate(
    empresa_id: str, connection_id: uuid.UUID, db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    connection = await get_connection(db, _empresa_id(empresa_id), connection_id)
    if not connection:
        raise HTTPException(404, "Conexão Instagram não encontrada.")
    if str(connection.status or "").lower() != "connected":
        raise HTTPException(409, "Conexão Instagram pendente de configuração e sem token operacional.")
    try:
        connection = await revalidate_connection(db, connection)
    except Exception as exc:
        raise _graph_error(exc) from exc
    return {"success": True, "connection": serialize_connection(connection)}


@router.post("/connection/{connection_id}/reconfigure")
async def reconfigure(
    empresa_id: str, connection_id: uuid.UUID, payload: SaveConnectionInput,
    db: AsyncSession = Depends(get_db), _: Usuario = Depends(require_tenant_access),
):
    tenant_id = _empresa_id(empresa_id)
    try:
        connection = await reconfigure_connection(
            db,
            empresa_id=tenant_id,
            connection_id=connection_id,
            professional_account_id=payload.account_id,
            access_token=payload.instagram_user_access_token,
            name=payload.name,
        )
    except Exception as exc:
        await db.rollback()
        if isinstance(exc, ValueError) and "não encontrada" in str(exc):
            raise HTTPException(404, "Conexão Instagram não encontrada.") from exc
        raise _graph_error(exc) from exc
    return {"success": True, "connection": serialize_connection(connection)}


@router.delete("/connection/{connection_id}")
async def delete_connection(
    empresa_id: str, connection_id: uuid.UUID, db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    connection = await get_connection(db, _empresa_id(empresa_id), connection_id)
    if not connection:
        raise HTTPException(404, "Conexão Instagram não encontrada.")
    await db.delete(connection)
    await db.commit()
    return {"success": True, "deleted": True}


@router.get("/media")
async def list_media(
    empresa_id: str, connection_id: uuid.UUID = Query(...), after: str | None = None,
    limit: int = Query(25, ge=1, le=100), db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    connection = await get_connection(db, _empresa_id(empresa_id), connection_id)
    if not connection:
        raise HTTPException(404, "Conexão Instagram não encontrada.")
    if str(connection.status or "").lower() != "connected":
        raise HTTPException(409, "Conexão Instagram pendente de configuração e sem token operacional.")
    try:
        body = await InstagramGraphClient(connection_token(connection)).media(
            str(connection.instagram_business_account_id), after=after, limit=limit,
        )
    except Exception as exc:
        raise _graph_error(exc) from exc
    paging = body.get("paging") if isinstance(body.get("paging"), dict) else {}
    cursors = paging.get("cursors") if isinstance(paging.get("cursors"), dict) else {}
    return {"success": True, "connection_id": str(connection.id), "media": body.get("data") or [], "paging": {"after": cursors.get("after")}}


@router.get("/automations")
async def list_automations(
    empresa_id: str, connection_id: uuid.UUID = Query(...), db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa = _empresa_id(empresa_id)
    if not await get_connection(db, empresa, connection_id):
        raise HTTPException(404, "Conexão Instagram não encontrada.")
    rows = (await db.execute(select(InstagramCommentAutomation).where(
        InstagramCommentAutomation.empresa_id == empresa,
        InstagramCommentAutomation.connection_id == connection_id,
    ).order_by(InstagramCommentAutomation.criado_em.desc()))).scalars().all()
    return {"success": True, "automations": [_automation_payload(row) for row in rows]}


@router.post("/automations", status_code=201)
async def create_automation(
    empresa_id: str, payload: AutomationInput, db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa = _empresa_id(empresa_id)
    connection = await get_connection(db, empresa, payload.connection_id)
    if not connection:
        raise HTTPException(404, "Conexão Instagram não encontrada.")
    row = InstagramCommentAutomation(
        empresa_id=empresa, connection_id=connection.id,
        instagram_account_id=connection.instagram_business_account_id,
        name=payload.name.strip(), active=payload.active, media_id=payload.media_id,
        keywords=[value.strip() for value in payload.keywords if value.strip()],
        public_reply_enabled=payload.public_reply_enabled, public_reply_mode="fixed",
        fixed_reply=payload.fixed_reply, fallback_reply=payload.fixed_reply or "Obrigada pelo interesse!",
        private_reply_enabled=payload.private_reply_enabled,
        private_message=payload.private_reply_config.text if payload.private_reply_config else payload.private_message,
        private_link=payload.private_link,
        private_reply_config=_private_reply_config_payload(payload),
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"success": True, "automation": _automation_payload(row)}


async def _owned_automation(db: AsyncSession, empresa: uuid.UUID, automation_id: uuid.UUID) -> InstagramCommentAutomation | None:
    return (await db.execute(select(InstagramCommentAutomation).where(
        InstagramCommentAutomation.id == automation_id,
        InstagramCommentAutomation.empresa_id == empresa,
    ))).scalars().first()


@router.put("/automations/{automation_id}")
async def update_automation(
    empresa_id: str, automation_id: uuid.UUID, payload: AutomationInput,
    db: AsyncSession = Depends(get_db), _: Usuario = Depends(require_tenant_access),
):
    empresa = _empresa_id(empresa_id)
    row = await _owned_automation(db, empresa, automation_id)
    connection = await get_connection(db, empresa, payload.connection_id)
    if not row or not connection:
        raise HTTPException(404, "Automação ou conexão Instagram não encontrada.")
    row.connection_id = connection.id
    row.instagram_account_id = connection.instagram_business_account_id
    row.name = payload.name.strip(); row.active = payload.active; row.media_id = payload.media_id
    row.keywords = [value.strip() for value in payload.keywords if value.strip()]
    row.public_reply_enabled = payload.public_reply_enabled; row.public_reply_mode = "fixed"
    row.fixed_reply = payload.fixed_reply; row.fallback_reply = payload.fixed_reply or "Obrigada pelo interesse!"
    row.private_reply_enabled = payload.private_reply_enabled
    row.private_message = payload.private_reply_config.text if payload.private_reply_config else payload.private_message
    row.private_link = payload.private_link
    row.private_reply_config = _private_reply_config_payload(payload)
    await db.commit(); await db.refresh(row)
    return {"success": True, "automation": _automation_payload(row)}


@router.delete("/automations/{automation_id}")
async def delete_automation(
    empresa_id: str, automation_id: uuid.UUID, db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    row = await _owned_automation(db, _empresa_id(empresa_id), automation_id)
    if not row:
        raise HTTPException(404, "Automação Instagram não encontrada.")
    await db.delete(row); await db.commit()
    return {"success": True, "deleted": True}


@router.get("/history")
async def history(
    empresa_id: str, connection_id: uuid.UUID = Query(...), limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db), _: Usuario = Depends(require_tenant_access),
):
    empresa = _empresa_id(empresa_id)
    if not await get_connection(db, empresa, connection_id):
        raise HTTPException(404, "Conexão Instagram não encontrada.")
    rows = (await db.execute(select(InstagramCommentExecution).where(
        InstagramCommentExecution.empresa_id == empresa,
        InstagramCommentExecution.connection_id == connection_id,
    ).order_by(InstagramCommentExecution.criado_em.desc()).limit(limit))).scalars().all()
    return {"success": True, "history": [{
        "id": str(row.id), "automation_id": str(row.automation_id), "comment_id": row.comment_id,
        "media_id": row.media_id, "username": row.username, "comment_text": row.comment_text,
        "public_reply": row.public_reply, "public_reply_status": row.public_reply_status,
        "private_reply": row.private_reply, "private_reply_status": row.private_reply_status,
        "status": row.status, "error_detail": row.error_detail,
        "created_at": row.criado_em.isoformat() if row.criado_em else None,
    } for row in rows]}

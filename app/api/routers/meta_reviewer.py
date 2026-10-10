"""Read-only Meta App Review surface with one tenant-scoped test-tag action."""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.auth import get_current_user
from app.agents.schemas import CommercialStateDecision
from app.services.commercial_state_executor import execute_commercial_state
from db.database import get_db
from db.models import BusinessMessagingConversionMapping, CRMLead, Conexao, Empresa, MetaConversionEvent, TagCRM, TipoConexao, Usuario, is_meta_app_reviewer_role

router = APIRouter(prefix="/api/reviewer", tags=["Meta App Review"])
TEST_TAG_NAME = "App Review - LeadSubmitted Test Event"


async def _reviewer(empresa_id: str, current_user: Usuario = Depends(get_current_user)) -> tuple[uuid.UUID, Usuario]:
    try:
        tenant_id = uuid.UUID(str(empresa_id))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="ID da empresa inválido.") from exc
    if not is_meta_app_reviewer_role(current_user.role) or current_user.empresa_id != tenant_id:
        raise HTTPException(status_code=403, detail="Acesso restrito à conta de análise.")
    return tenant_id, current_user


def _masked_id(value: Any) -> str | None:
    raw = str(value or "").strip()
    return f"***{raw[-4:]}" if raw else None


async def _load_review_data(db: AsyncSession, tenant_id: uuid.UUID) -> dict[str, Any]:
    empresa = (await db.execute(select(Empresa).where(Empresa.id == tenant_id))).scalars().first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    conexao = (await db.execute(select(Conexao).where(Conexao.empresa_id == tenant_id, Conexao.tipo == TipoConexao.META, Conexao.provider == "meta_official"))).scalars().first()
    tag = (await db.execute(select(TagCRM).where(TagCRM.empresa_id == tenant_id, TagCRM.nome == TEST_TAG_NAME))).scalars().first()
    mapping = None
    if tag:
        mapping = (
            await db.execute(
                select(BusinessMessagingConversionMapping).where(
                    BusinessMessagingConversionMapping.empresa_id == tenant_id,
                    BusinessMessagingConversionMapping.trigger_type == "tag_applied",
                    BusinessMessagingConversionMapping.trigger_ref == tag.id,
                    BusinessMessagingConversionMapping.event_name == "LeadSubmitted",
                )
            )
        ).scalars().first()
    lead = None
    if tag:
        candidates = (await db.execute(select(CRMLead).where(CRMLead.empresa_id == tenant_id).order_by(CRMLead.criado_em.desc()))).scalars().all()
        lead = next((item for item in candidates if isinstance(item.dados_adicionais, dict) and isinstance(item.dados_adicionais.get("meta_attribution"), dict) and item.dados_adicionais.get("meta_attribution", {}).get("last_touch", {}).get("ctwa_clid")), None)
    latest = None
    if tag and lead:
        latest = (await db.execute(select(MetaConversionEvent).where(MetaConversionEvent.empresa_id == tenant_id, MetaConversionEvent.tag_id == tag.id, MetaConversionEvent.lead_id == lead.id).order_by(MetaConversionEvent.criado_em.desc()))).scalars().first()
    return {
        "empresa": {"id": _masked_id(empresa.id), "nome": empresa.nome_empresa},
        "conexao_whatsapp": {"status": str(conexao.status) if conexao else "disconnected", "provider": "meta_official" if conexao else None, "waba_id": _masked_id(conexao.waba_id) if conexao else None},
        "tag_teste": {"nome": TEST_TAG_NAME, "visivel": bool(tag), "id": _masked_id(tag.id) if tag else None, "aplicada": bool(tag and lead and str(tag.id) in (lead.tags or [])), "configuracao": {"ativo": bool(mapping and mapping.ativo), "event_name": str(mapping.event_name) if mapping else None, "mode": str(mapping.send_mode) if mapping else None, "contexto": "business_messaging_whatsapp" if mapping else None, "credential_source": "embedded_signup_tenant" if mapping else None}},
        "contato_teste": {"disponivel": bool(lead), "rotulo": "Contato de teste CTWA", "origem": "click_to_whatsapp", "atribuicao_disponivel": bool(lead), "conversao_status": str(latest.status) if latest else None},
    }


@router.get("/{empresa_id}/overview")
async def reviewer_overview(empresa_id: str, db: AsyncSession = Depends(get_db), reviewer: tuple[uuid.UUID, Usuario] = Depends(_reviewer)):
    return await _load_review_data(db, reviewer[0])


@router.post("/{empresa_id}/apply-test-tag")
async def reviewer_apply_test_tag(empresa_id: str, reviewer: tuple[uuid.UUID, Usuario] = Depends(_reviewer), db: AsyncSession = Depends(get_db)):
    tenant_id = reviewer[0]
    data = await _load_review_data(db, tenant_id)
    if not data["tag_teste"]["visivel"] or not data["contato_teste"]["disponivel"]:
        raise HTTPException(status_code=404, detail="Demonstração preparada indisponível.")
    tag = (await db.execute(select(TagCRM).where(TagCRM.empresa_id == tenant_id, TagCRM.nome == TEST_TAG_NAME))).scalars().first()
    lead = (await db.execute(select(CRMLead).where(CRMLead.empresa_id == tenant_id).order_by(CRMLead.criado_em.desc()))).scalars().all()
    lead = next((item for item in lead if isinstance(item.dados_adicionais, dict) and isinstance(item.dados_adicionais.get("meta_attribution"), dict) and item.dados_adicionais.get("meta_attribution", {}).get("last_touch", {}).get("ctwa_clid")), None)
    result = await execute_commercial_state(
        db,
        empresa_id=tenant_id,
        lead_id=lead.id,
        decision=CommercialStateDecision(tags_to_apply=[str(tag.id)]),
        source="system",
        source_event_id="meta_app_review",
    )
    return {"status": "ok", "tag_aplicada": True, "contato": "Contato de teste CTWA", "resultado": "aplicação processada", "detail": "tag_test_applied"}

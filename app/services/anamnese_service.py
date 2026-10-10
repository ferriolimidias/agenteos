from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Mapping

from sqlalchemy import select

from app.services.tokenized_links import (
    TOKENIZED_LINKS_KEY,
    generate_or_get_tokenized_link,
)
from db.database import AsyncSessionLocal
from db.models import CRMLead, ChannelConversation, Empresa


class AnamneseServiceError(RuntimeError):
    pass


@dataclass(frozen=True)
class AnamneseLink:
    success: bool
    created: bool
    reused: bool
    anamnese_id: str
    url: str
    link_slug: str
    empresa_id: str
    lead_id: str
    conversation_id: str | None


async def criar_ou_obter_anamnese(
    *,
    empresa_id: str,
    lead_id: str,
    conversation_id: str | None = None,
) -> AnamneseLink:
    """Cria ou recupera o link de anamnese no registro canônico do CRMLead.

    O lock da linha serializa duas intenções simultâneas para o mesmo lead. O
    token bruto nunca é salvo; a infraestrutura de links guarda somente hash e
    ciphertext e reconstrói a mesma URL enquanto ela estiver válida.
    """

    try:
        empresa_uuid = uuid.UUID(str(empresa_id))
        lead_uuid = uuid.UUID(str(lead_id))
    except (TypeError, ValueError) as exc:
        raise AnamneseServiceError("empresa_id ou lead_id invalido") from exc

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(CRMLead, Empresa)
            .join(Empresa, CRMLead.empresa_id == Empresa.id)
            .where(CRMLead.id == lead_uuid, CRMLead.empresa_id == empresa_uuid)
            .with_for_update(of=CRMLead)
        )
        row = result.first()
        if not row:
            raise AnamneseServiceError("lead nao encontrado para a empresa")
        lead, empresa = row

        verified_conversation_id = await _verified_conversation_id(
            session,
            empresa_id=empresa_uuid,
            lead_id=lead_uuid,
            conversation_id=conversation_id,
        )
        link_slug = _anamnese_link_slug(empresa)
        if not link_slug:
            raise AnamneseServiceError("configuracao de anamnese ausente ou inativa")

        link = generate_or_get_tokenized_link(
            session,
            empresa,
            lead,
            link_slug,
            conversation_id=verified_conversation_id,
        )
        if link.operational_pending or not link.url or not link.record:
            raise AnamneseServiceError(str(link.reason or "falha ao gerar link de anamnese"))
        await session.commit()

        return AnamneseLink(
            success=True,
            created=not link.reused,
            reused=bool(link.reused),
            anamnese_id=str(link.record.get("anamnese_id") or ""),
            url=str(link.url),
            link_slug=link_slug,
            empresa_id=str(empresa_uuid),
            lead_id=str(lead_uuid),
            conversation_id=verified_conversation_id,
        )


def _anamnese_link_slug(empresa: Empresa | Mapping[str, Any]) -> str | None:
    credentials = (
        empresa.get("credenciais_canais")
        if isinstance(empresa, Mapping)
        else getattr(empresa, "credenciais_canais", None)
    )
    configs = credentials.get(TOKENIZED_LINKS_KEY) if isinstance(credentials, Mapping) else None
    if not isinstance(configs, Mapping):
        return None
    candidates: list[str] = []
    for slug, raw in configs.items():
        if not isinstance(raw, Mapping) or not bool(raw.get("ativo", True)):
            continue
        if str(raw.get("finalidade") or "").strip().lower() != "anamnese":
            continue
        candidates.append(str(slug))
    if "anamnese_leoft" in candidates:
        return "anamnese_leoft"
    return sorted(candidates)[0] if candidates else None


async def _verified_conversation_id(
    session,
    *,
    empresa_id: uuid.UUID,
    lead_id: uuid.UUID,
    conversation_id: str | None,
) -> str | None:
    raw = str(conversation_id or "").strip()
    if not raw:
        return None
    try:
        conversation_uuid = uuid.UUID(raw)
    except (TypeError, ValueError) as exc:
        raise AnamneseServiceError("conversation_id invalido") from exc
    result = await session.execute(
        select(ChannelConversation.id).where(
            ChannelConversation.id == conversation_uuid,
            ChannelConversation.empresa_id == empresa_id,
            ChannelConversation.lead_id == lead_id,
        )
    )
    if result.scalar_one_or_none() is None:
        raise AnamneseServiceError("conversa nao pertence ao lead e empresa")
    return str(conversation_uuid)

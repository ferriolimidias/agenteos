from __future__ import annotations

import hashlib
import logging
import re
import time
import uuid

import httpx
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import AsyncSessionLocal
from db.models import CRMLead, Empresa, TagCRM

logger = logging.getLogger(__name__)

GRAPH_CAPI_VERSION = "v19.0"

_VENDA_TAG_SUBSTRINGS = (
    "venda",
    "compra",
    "pago",
    "pagamento",
    "purchase",
    "recebido",
    "conversao financeira",
    "conversão financeira",
    "valor recebido",
)

_QUALIFICACAO_TAG_SUBSTRINGS = (
    "qualificado",
    "qualificação",
    "qualificacao",
    "mql",
    "sql",
    "interesse",
    "nutrição",
    "nutricao",
    "prospecção",
    "prospeccao",
    "oportunidade",
)


def _digits_only(value: str | None) -> str:
    return re.sub(r"\D", "", str(value or ""))


def normalizar_telefone_para_meta_ph(telefone: str | None) -> str | None:
    """
    Remove +, -, (), espaços e não numéricos.
    Garante DDI 55 (Brasil) quando o número parece nacional (10–11 dígitos sem DDI).
    """
    digits = _digits_only(telefone)
    if not digits:
        return None
    if digits.startswith("55"):
        return digits
    if 10 <= len(digits) <= 11 and not digits.startswith("0"):
        return "55" + digits
    return digits


def hashear_ph_meta(telefone_normalizado: str) -> str:
    """SHA-256 em hexadecimal (minúsculas), conforme especificação Meta para user_data.ph."""
    return hashlib.sha256(telefone_normalizado.encode("utf-8")).hexdigest()


def _tag_indica_venda(tag: TagCRM) -> bool:
    nome = str(tag.nome or "").lower()
    return any(s in nome for s in _VENDA_TAG_SUBSTRINGS)


def _tag_indica_qualificacao(tag: TagCRM) -> bool:
    nome = str(tag.nome or "").lower()
    return any(s in nome for s in _QUALIFICACAO_TAG_SUBSTRINGS)


def _resolver_tipo_evento_e_valor(lead: CRMLead, tag: TagCRM) -> tuple[str, dict | None]:
    """
    Purchase: valor_conversao > 0 ou nome da tag sugere venda (custom_data obrigatório).
    Lead: nome da tag sugere qualificação (sem Purchase) ou caso padrão.
    Valor em Purchase: float(lead.valor_conversao) conforme contrato da API.
    """
    valor_conv = float(lead.valor_conversao or 0)
    if valor_conv > 0 or _tag_indica_venda(tag):
        return "Purchase", {"currency": "BRL", "value": float(lead.valor_conversao or 0)}
    if _tag_indica_qualificacao(tag):
        return "Lead", None
    return "Lead", None


async def enviar_evento_meta_capi(
    lead: CRMLead,
    tag: TagCRM,
    empresa: Empresa,
    db_session: AsyncSession,
) -> None:
    """
    Envia um único evento (Lead ou Purchase) à Meta Conversions API.
    db_session é mantido na assinatura para compatibilidade com chamadas em transação;
    não é obrigatório utilizá-lo no corpo.
    """
    _ = db_session

    if not bool(getattr(empresa, "meta_capi_ativo", False)):
        logger.info(
            "[META CAPI] Envio ignorado (meta_capi_ativo=False) empresa_id=%s lead_id=%s",
            getattr(empresa, "id", None),
            getattr(lead, "id", None),
        )
        return

    pixel_id = str(getattr(empresa, "meta_pixel_id", "") or "").strip()
    access_token = str(getattr(empresa, "meta_access_token", "") or "").strip()
    if not pixel_id or not access_token:
        logger.warning(
            "[META CAPI] Envio ignorado (pixel_id ou access_token vazio) empresa_id=%s",
            getattr(empresa, "id", None),
        )
        return

    tel_norm = normalizar_telefone_para_meta_ph(getattr(lead, "telefone_contato", None))
    if not tel_norm:
        logger.warning(
            "[META CAPI] Envio ignorado (telefone inválido após normalização) lead_id=%s",
            getattr(lead, "id", None),
        )
        return

    user_data: dict = {"ph": [hashear_ph_meta(tel_norm)]}
    fbclid = str(getattr(lead, "fbclid", "") or "").strip()
    if fbclid:
        # Formato recomendado para fbc a partir de fbclid em ambiente server-side.
        user_data["fbc"] = f"fb.1.{int(time.time() * 1000)}.{fbclid}"

    event_name, custom_data = _resolver_tipo_evento_e_valor(lead, tag)
    event_time = int(time.time())
    event_item: dict = {
        "event_name": event_name,
        "event_time": event_time,
        "action_source": "chat",
        "event_id": f"{lead.id}:{tag.id}:{event_time}",
        "user_data": user_data,
    }
    if custom_data is not None:
        event_item["custom_data"] = custom_data

    url = f"https://graph.facebook.com/{GRAPH_CAPI_VERSION}/{pixel_id}/events"
    params = {"access_token": access_token}
    payload = {"data": [event_item]}

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(url, params=params, json=payload)
            if response.status_code >= 400:
                body: object
                try:
                    body = response.json()
                except Exception:
                    body = response.text
                logger.error(
                    "[META CAPI] HTTP %s | lead_id=%s tag=%s event=%s | resposta=%s",
                    response.status_code,
                    lead.id,
                    getattr(tag, "nome", None),
                    event_name,
                    body,
                )
                return
        logger.info(
            "[META CAPI] Evento enviado | lead_id=%s tag=%s event=%s http=%s",
            lead.id,
            getattr(tag, "nome", None),
            event_name,
            response.status_code,
        )
    except httpx.HTTPError as exc:
        logger.error(
            "[META CAPI] Falha de rede/HTTP | lead_id=%s tag=%s event=%s erro=%s",
            getattr(lead, "id", None),
            getattr(tag, "nome", None),
            event_name,
            exc,
            exc_info=True,
        )
    except Exception:
        logger.exception(
            "[META CAPI] Erro inesperado | lead_id=%s tag=%s event=%s",
            getattr(lead, "id", None),
            getattr(tag, "nome", None),
            event_name,
        )


async def disparar_meta_capi_por_tag(lead_id: str, tag_nome: str) -> None:
    """
    Carrega lead/tag/empresa em nova sessão e envia à Meta.
    Adequado para BackgroundTasks do FastAPI (não reutiliza sessão da requisição).
    """
    try:
        lead_uuid = uuid.UUID(str(lead_id))
    except (ValueError, TypeError):
        logger.warning("[META CAPI] lead_id inválido: %s", lead_id)
        return

    async with AsyncSessionLocal() as session:
        lead = (await session.execute(select(CRMLead).where(CRMLead.id == lead_uuid))).scalars().first()
        if not lead:
            logger.warning("[META CAPI] Lead não encontrado: %s", lead_id)
            return

        tag = (
            await session.execute(
                select(TagCRM).where(
                    TagCRM.empresa_id == lead.empresa_id,
                    func.lower(TagCRM.nome) == str(tag_nome or "").strip().lower(),
                )
            )
        ).scalars().first()
        if not tag:
            logger.warning("[META CAPI] Tag não encontrada: %s (lead=%s)", tag_nome, lead_id)
            return

        if not bool(getattr(tag, "disparar_conversao_ads", False)):
            logger.info("[META CAPI] Tag sem disparar_conversao_ads: %s (lead=%s)", tag_nome, lead_id)
            return

        empresa = (await session.execute(select(Empresa).where(Empresa.id == lead.empresa_id))).scalars().first()
        if not empresa:
            logger.warning("[META CAPI] Empresa não encontrada para lead=%s", lead_id)
            return

        await enviar_evento_meta_capi(lead, tag, empresa, session)


async def notificar_conversao_ads(lead_id: str, tag_nome: str, db: AsyncSession | None = None) -> None:
    """Compatível com chamadas legadas; `db` é ignorado (nova sessão em `disparar_meta_capi_por_tag`)."""
    await disparar_meta_capi_por_tag(lead_id, tag_nome)

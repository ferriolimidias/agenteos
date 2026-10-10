from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from app.agents.schemas import CommercialStateDecision
from app.services.commercial_state_executor import execute_commercial_state
from app.services.leoft_checkout_service import (
    LeoFTCheckoutError,
    create_or_get_checkout,
)
from app.services.leoft_funnel_events import record_leoft_funnel_event
from app.services.leoft_security import sanitize_payload_for_storage
from app.services.tokenized_links import (
    TOKENIZED_LINKS_KEY,
    tokenized_link_token_hash,
    validate_tokenized_link,
)
from db.database import AsyncSessionLocal, get_db
from db.models import CRMLead, Empresa, TagCRM

router = APIRouter(prefix="/api/public/anamnese", tags=["Public Anamnese"])
logger = logging.getLogger(__name__)


class AnamneseSubmitPayload(BaseModel):
    token: str = Field(min_length=8, max_length=512)
    respostas: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class AnamneseEventPayload(BaseModel):
    token: str = Field(min_length=8, max_length=512)
    event_type: str = Field(min_length=3, max_length=80)
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.get("/{slug}/session")
async def public_anamnese_session(
    slug: str,
    token: str = Query(..., min_length=8, max_length=512),
    db: AsyncSession = Depends(get_db),
):
    validation = await validate_tokenized_link(db, public_slug=slug, token=token)
    if not validation.ok:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link inválido ou expirado.")

    lead = validation.lead
    config = validation.config or {}
    anamneses = (lead.dados_adicionais or {}).get("anamneses", {})
    anamnese = _get_anamnese_record(anamneses, validation.link_slug or validation.slug) or _get_anamnese_record(
        anamneses,
        validation.slug,
    )
    return {
        "ok": True,
        "slug": validation.slug,
        "oferta_nome": _safe_text(config.get("oferta_nome") or config.get("nome"), default="Anamnese"),
        "lead_nome": _safe_first_name(getattr(lead, "nome_contato", None)),
        "checkout": _safe_checkout(anamnese),
        "status": "valid",
    }


@router.post("/{slug}/respostas")
async def public_anamnese_respostas(
    slug: str,
    payload: AnamneseSubmitPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    validation = await validate_tokenized_link(db, public_slug=slug, token=payload.token)
    if not validation.ok or not validation.lead or not validation.empresa:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link inválido ou expirado.")

    lead = validation.lead
    empresa = validation.empresa
    config = validation.config or {}
    link_slug = str(validation.link_slug or slug).strip()
    now = _utc_iso()
    dados = dict(lead.dados_adicionais or {}) if isinstance(lead.dados_adicionais, dict) else {}
    anamneses = dict(dados.get("anamneses") or {}) if isinstance(dados.get("anamneses"), dict) else {}
    current = _get_anamnese_record(anamneses, link_slug) or _get_anamnese_record(anamneses, slug) or {}
    already_submitted = bool(current.get("submitted"))

    tag_applied = False
    capi_status: str | None = None

    if not already_submitted:
        metadata = _safe_metadata(payload.metadata, request)
        raw_respostas = dict(payload.respostas or {})
        raw_personal = dict(raw_respostas.get("personalData") or {})
        # Profile data is authoritative on the server. Browser values are only
        # used for fields that are genuinely absent from the lead.
        canonical_phone = _lead_phone(lead)
        if not canonical_phone:
            logger.error(
                "leoft.anamnese.contact_missing empresa_id=%s lead_id=%s field=phone",
                str(empresa.id), str(lead.id),
            )
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Vínculo de telefone do lead indisponível.")
        raw_personal.pop("whatsapp", None)
        raw_personal["whatsapp"] = canonical_phone
        raw_personal.pop("email", None)
        raw_respostas["personalData"] = raw_personal
        respostas = sanitize_payload_for_storage(raw_respostas, token=payload.token)
        current.update(
            {
                "submitted": True,
                "submitted_at": now,
                "respostas": respostas,
                "metadata": metadata,
                "token_ref": tokenized_link_token_hash(payload.token),
                "origem": "public_anamnese",
                "versao_form": _safe_text(config.get("versao_form"), default="lovable_v1"),
            }
        )
        anamneses[link_slug] = current
        dados["anamneses"] = anamneses
        _mark_link_submitted(dados, link_slug, now)
        lead.dados_adicionais = dados
        flag_modified(lead, "dados_adicionais")

        tag_name = _safe_text(config.get("tag_conclusao"), default="")
        if tag_name and not _is_e2e_safe_metadata(metadata):
            tag_applied, capi_status = await _apply_completion_tag(
                db,
                lead=lead,
                empresa=empresa,
                tag_name=tag_name,
            )
        elif tag_name:
            capi_status = "skipped_e2e_safe"
        db.add(lead)
        await record_leoft_funnel_event(
            db,
            empresa_id=empresa.id,
            lead_id=lead.id,
            event_type="anamnese_completed",
            source="public_anamnese_submit",
            anamnese_id=str(validation.record.get("anamnese_id") or f"anamnese:{lead.id}"),
            metadata={"slug": slug, "version": current.get("versao_form")},
        )
        await db.commit()

    # The completion must be idempotent. A row lock prevents a refresh/double
    # submit from issuing two Mercado Pago preferences for the same lead.
    checkout = None
    checkout_error = None
    if bool(config.get("checkout_no_final")) and str(config.get("checkout_provider") or "").lower() == "mercado_pago":
        # Re-acquire the canonical lead lock after the answer commit. This
        # serializes refresh/double-submit checkout creation.
        locked = await db.execute(select(CRMLead).where(CRMLead.id == lead.id).with_for_update())
        lead = locked.scalar_one()
        dados = dict(lead.dados_adicionais or {}) if isinstance(lead.dados_adicionais, dict) else {}
        anamneses = dict(dados.get("anamneses") or {}) if isinstance(dados.get("anamneses"), dict) else {}
        checkout_data = _get_anamnese_record(anamneses, link_slug) or _get_anamnese_record(anamneses, slug) or current
        if checkout_data.get("submitted"):
            try:
                logger.info(
                    "leoft.checkout.started empresa_id=%s lead_id=%s anamnese_id=%s provider=%s",
                    str(empresa.id), str(lead.id), str(validation.record.get("anamnese_id") or "")[:80],
                    str(config.get("checkout_provider") or "mercado_pago"),
                )
                checkout_result = await create_or_get_checkout(
                    anamnese=checkout_data,
                    empresa_id=str(empresa.id),
                    lead_id=str(lead.id),
                    anamnese_id=str(validation.record.get("anamnese_id") or f"anamnese:{lead.id}"),
                    config=config,
                    # LeoFT does not require payer contact data for the
                    # preference. Keep identity in external_reference and do
                    # not send the CRM phone in an invalid string shape.
                    payer=None,
                    db=db,
                )
                checkout = {
                    "reference": checkout_result.reference,
                    "url": checkout_result.url,
                    "provider": checkout_result.provider,
                    "preference_id": checkout_result.preference_id,
                }
                checkout_data["checkout"] = checkout
                checkout_data["payment_screen_viewed_at"] = checkout_data.get("payment_screen_viewed_at") or now
                anamneses[link_slug] = checkout_data
                dados["anamneses"] = anamneses
                lead.dados_adicionais = dados
                flag_modified(lead, "dados_adicionais")
                db.add(lead)
                await record_leoft_funnel_event(
                    db,
                    empresa_id=empresa.id,
                    lead_id=lead.id,
                    event_type="checkout_created",
                    source="public_anamnese_submit",
                    anamnese_id=str(validation.record.get("anamnese_id") or f"anamnese:{lead.id}"),
                    checkout_reference=checkout_result.reference,
                    metadata={
                        "provider": checkout_result.provider,
                        "preference_id_present": bool(checkout_result.preference_id),
                        "created": bool(checkout_result.created),
                        "reused": bool(checkout_result.reused),
                    },
                )
                await record_leoft_funnel_event(
                    db,
                    empresa_id=empresa.id,
                    lead_id=lead.id,
                    event_type="payment_screen_viewed",
                    source="public_anamnese_submit",
                    anamnese_id=str(validation.record.get("anamnese_id") or f"anamnese:{lead.id}"),
                    checkout_reference=checkout_result.reference,
                    metadata={"provider": checkout_result.provider},
                )
                try:
                    from app.services.meta.business_messaging_conversion_engine import (
                        ConversionDomainEvent,
                        emit_conversion_domain_event,
                    )

                    await emit_conversion_domain_event(
                        db,
                        ConversionDomainEvent(
                            empresa_id=empresa.id,
                            event_type="checkout_created",
                            lead_id=lead.id,
                            source_event_id=f"checkout_created:{checkout_result.reference}",
                            trigger_source="system",
                            event_time=int(datetime.utcnow().timestamp()),
                            payload={
                                "checkout_id": checkout_result.preference_id,
                                "checkout_reference": checkout_result.reference,
                                "provider": checkout_result.provider,
                                "amount": config.get("checkout_amount"),
                                "currency": config.get("checkout_currency") or "BRL",
                                "order_id": checkout_result.reference,
                            },
                        ),
                    )
                except Exception as conversion_exc:
                    logger.warning(
                        "leoft.checkout.conversion_trigger_skipped empresa_id=%s lead_id=%s error_class=%s",
                        str(empresa.id), str(lead.id), conversion_exc.__class__.__name__,
                    )
                await db.commit()
                # Checkout/funnel data is already durable before optional
                # attribution enrichment. The ledger can be retried from the
                # canonical funnel event if this side write is unavailable.
                try:
                    from app.services.attribution_ledger import record_checkout_touch

                    async with AsyncSessionLocal() as attribution_session:
                        await record_checkout_touch(
                            attribution_session,
                            empresa_id=empresa.id,
                            lead_id=lead.id,
                            checkout_reference=checkout_result.reference,
                            checkout_id=checkout_result.preference_id,
                            provider=checkout_result.provider,
                            occurred_at=datetime.now().astimezone(),
                        )
                        await attribution_session.commit()
                except Exception as attribution_exc:
                    logger.warning(
                        "event=ATTRIBUTION_CHECKOUT_CAPTURE_FAILED exception_type=%s retryable=true recovery=funnel_event",
                        type(attribution_exc).__name__,
                    )
                logger.info(
                    "leoft.checkout.finished empresa_id=%s lead_id=%s anamnese_id=%s outcome=%s",
                    str(empresa.id), str(lead.id), str(validation.record.get("anamnese_id") or "")[:80],
                    "created" if checkout_result.created else "reused",
                )
            except LeoFTCheckoutError as exc:
                # Completion remains successful; the UI can offer a retry on
                # a later refresh without claiming that payment was started.
                logger.warning(
                    "leoft.checkout.failed empresa_id=%s lead_id=%s anamnese_id=%s error_class=%s reason=%s",
                    str(empresa.id), str(lead.id), str(validation.record.get("anamnese_id") or "")[:80],
                    exc.__class__.__name__, str(exc)[:120],
                )
                checkout = None
                checkout_error = "checkout_unavailable"
                await record_leoft_funnel_event(
                    db,
                    empresa_id=empresa.id,
                    lead_id=lead.id,
                    event_type="checkout_failed",
                    source="public_anamnese_submit",
                    anamnese_id=str(validation.record.get("anamnese_id") or f"anamnese:{lead.id}"),
                    metadata={"error": str(exc)[:120]},
                )
                await db.commit()

    return {
        "ok": True,
        "slug": slug,
        "status": "already_submitted" if already_submitted else "submitted",
        "message": "Anamnese enviada com sucesso.",
        "tag_applied": tag_applied,
        "capi_status": capi_status,
        "checkout": checkout,
        "checkout_error": checkout_error,
    }


@router.post("/{slug}/event")
async def public_anamnese_event(
    slug: str,
    payload: AnamneseEventPayload,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    validation = await validate_tokenized_link(db, public_slug=slug, token=payload.token)
    if not validation.ok or not validation.lead or not validation.empresa:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Link inválido ou expirado.")
    allowed = {
        "anamnese_started",
        "payment_screen_viewed",
        "payment_button_clicked",
    }
    event_type = str(payload.event_type or "").strip()
    if event_type not in allowed:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Evento inválido.")
    lead = validation.lead
    dados = dict(lead.dados_adicionais or {}) if isinstance(lead.dados_adicionais, dict) else {}
    anamneses = dict(dados.get("anamneses") or {}) if isinstance(dados.get("anamneses"), dict) else {}
    anamnese = _get_anamnese_record(anamneses, validation.link_slug or slug) or {}
    checkout = anamnese.get("checkout") if isinstance(anamnese.get("checkout"), dict) else {}
    await record_leoft_funnel_event(
        db,
        empresa_id=validation.empresa.id,
        lead_id=lead.id,
        event_type=event_type,
        source="public_anamnese_browser",
        anamnese_id=str(validation.record.get("anamnese_id") or f"anamnese:{lead.id}"),
        checkout_reference=str(checkout.get("reference") or "") or None,
        metadata=_safe_metadata(payload.metadata, request),
    )
    await db.commit()
    return {"ok": True}


def _lead_phone(lead: CRMLead) -> str | None:
    value = str(getattr(lead, "telefone_contato", None) or "").strip()
    return value or None


def _get_anamnese_record(anamneses: Any, key: Any) -> dict[str, Any] | None:
    if not isinstance(anamneses, dict):
        return None
    raw = anamneses.get(str(key or "").strip())
    return dict(raw) if isinstance(raw, dict) else None


def _safe_checkout(anamnese: Any) -> dict[str, Any] | None:
    checkout = anamnese.get("checkout") if isinstance(anamnese, dict) else None
    if not isinstance(checkout, dict) or not checkout.get("url"):
        return None
    return {"url": str(checkout["url"]), "provider": str(checkout.get("provider") or "mercado_pago")}


async def _apply_completion_tag(
    db: AsyncSession,
    *,
    lead: CRMLead,
    empresa: Empresa,
    tag_name: str,
) -> tuple[bool, str | None]:
    result = await db.execute(
        select(TagCRM).where(
            TagCRM.empresa_id == lead.empresa_id,
            func.lower(TagCRM.nome) == tag_name.lower(),
        )
    )
    tag = result.scalars().first()
    if not tag:
        result = await db.execute(select(TagCRM).where(TagCRM.empresa_id == lead.empresa_id, TagCRM.nome.ilike(tag_name)))
        tag = result.scalars().first()
    if not tag:
        return False, "tag_not_found"

    tag_id = str(tag.id)
    if tag_id in {str(item).strip() for item in (lead.tags or [])}:
        return False, "tag_already_present"
    result = await execute_commercial_state(
        db,
        empresa_id=empresa.id,
        lead_id=lead.id,
        decision=CommercialStateDecision(tags_to_apply=[tag_id]),
        source="integration",
        source_event_id="public_anamnese",
    )
    return True, str(result.get("status") or "applied")


def _mark_link_submitted(dados: dict[str, Any], link_slug: str, submitted_at: str) -> None:
    links = dict(dados.get(TOKENIZED_LINKS_KEY) or {}) if isinstance(dados.get(TOKENIZED_LINKS_KEY), dict) else {}
    current = dict(links.get(link_slug) or {}) if isinstance(links.get(link_slug), dict) else {}
    if current:
        current["usado_em"] = current.get("usado_em") or submitted_at
        current["submitted"] = True
        current["submitted_at"] = submitted_at
        links[link_slug] = current
        dados[TOKENIZED_LINKS_KEY] = links


def _safe_metadata(metadata: dict[str, Any], request: Request) -> dict[str, Any]:
    safe = dict(metadata or {})
    safe["user_agent"] = str(safe.get("user_agent") or request.headers.get("user-agent") or "")[:500]
    safe["submitted_at_server"] = _utc_iso()
    if "submitted_at_client" in safe:
        safe["submitted_at_client"] = str(safe.get("submitted_at_client") or "")[:80]
    return safe


def _is_e2e_safe_metadata(metadata: dict[str, Any]) -> bool:
    return bool(metadata.get("e2e_leoft") or metadata.get("e2e_no_external_side_effects"))


def _safe_first_name(name: Any) -> str | None:
    cleaned = " ".join(str(name or "").split()).strip()
    if not cleaned:
        return None
    return cleaned.split(" ", 1)[0][:40]


def _safe_text(value: Any, *, default: str) -> str:
    cleaned = " ".join(str(value or "").split()).strip()
    return cleaned[:120] if cleaned else default


def _utc_iso() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"

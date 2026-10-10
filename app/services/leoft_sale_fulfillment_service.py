"""Central, idempotent LeoFT SALE_CONFIRMED fulfillment."""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm.attributes import flag_modified

from app.core.security import get_password_hash
from app.services.leoft_security import hash_token, mask_token
from app.services.outbox_service import deterministic_idempotency_key, enqueue_message
from app.services.whatsapp_address import normalize_whatsapp_address
from db.leoft_models import (
    LeoFTOfferFulfillment,
    LeoFTPersonalizationRequest,
    LeoFTSaleFulfillment,
    LeoFTStudent,
    LeoFTStudentAccess,
)
from db.models import CRMEtapa, CRMFunil, CRMLead, ChannelContact, TagCRM


SALE_EVENT_TYPE = "SALE_CONFIRMED"
CONFIRMED_PAYMENT_STATUS = "approved"
DEFAULT_PRODUCT_ID = "leoft_metodo_hibrido"
DEFAULT_PRODUCT_TYPE = "PERSONALIZED_PROGRAM"
DEFAULT_FULFILLMENT_TYPE = "PROGRAM_AND_APP"
ACTIVE_FULFILLMENT_STATUSES = {"received", "processing", "partial", "manual_review"}
PRODUCT_TYPES = {"APP_ACCESS", "STANDARD_PROGRAM", "PERSONALIZED_PROGRAM", "COACHING"}
FULFILLMENT_TYPES = {
    "ACCESS_ONLY",
    "STANDARD_PROGRAM_COPY",
    "PERSONALIZED_PROGRAM_REQUEST",
    "MANUAL_SERVICE",
    # V1 compatibility: this value means a standard program copy plus access.
    "PROGRAM_AND_APP",
}


class SaleFulfillmentError(RuntimeError):
    def __init__(self, code: str, message: str | None = None):
        self.code = str(code)
        super().__init__(message or self.code)


async def fulfill_sale_confirmed(
    session,
    *,
    payment: Mapping[str, Any],
    lead: CRMLead,
    anamnese_id: str | None,
    environment: str = "production",
    force_retry: bool = False,
) -> dict[str, Any]:
    """Process one approved payment without duplicating any local entity."""

    status = str(payment.get("status") or "").strip().lower()
    if status != CONFIRMED_PAYMENT_STATUS:
        raise SaleFulfillmentError("payment_not_confirmed")
    payment_id = _required(payment.get("id"), "payment_id_missing")
    company_id = uuid.UUID(str(lead.empresa_id))
    provider = str(payment.get("provider") or "mercado_pago").strip().lower()
    metadata = payment.get("metadata") if isinstance(payment.get("metadata"), Mapping) else {}
    order_id = str(payment.get("external_reference") or metadata.get("order_id") or "").strip()
    if not order_id:
        raise SaleFulfillmentError("order_id_missing")

    existing = await session.scalar(
        select(LeoFTSaleFulfillment)
        .where(
            LeoFTSaleFulfillment.company_id == company_id,
            LeoFTSaleFulfillment.payment_provider == provider,
            LeoFTSaleFulfillment.payment_id == payment_id,
        )
        .with_for_update()
    )
    if existing is not None:
        if existing.status == "completed" or not force_retry:
            return _result(existing, duplicate=True)
        sale = existing
    else:
        product_id = _product_id(payment, metadata)
        contact = await _resolve_contact(session, lead)
        sale = LeoFTSaleFulfillment(
            event_id=uuid.uuid4(),
            event_type=SALE_EVENT_TYPE,
            occurred_at=_payment_datetime(payment),
            company_id=company_id,
            sale_id=order_id,
            order_id=order_id,
            payment_id=payment_id,
            payment_provider=provider,
            payment_status="confirmed",
            paid_at=_payment_datetime(payment),
            product_id=product_id,
            offer_code=str(metadata.get("offer_code") or product_id)[:160],
            product_type=str(metadata.get("product_type") or DEFAULT_PRODUCT_TYPE)[:80],
            fulfillment_type=str(metadata.get("fulfillment_type") or DEFAULT_FULFILLMENT_TYPE)[:80],
            amount=_decimal_or_none(payment.get("transaction_amount") or metadata.get("amount")),
            currency=str(payment.get("currency_id") or metadata.get("currency") or "BRL").upper()[:3],
            lead_id=lead.id,
            contact_id=contact.id if contact is not None else None,
            anamnesis_id=str(anamnese_id or metadata.get("anamnesis_id") or "")[:160] or None,
            source=str(metadata.get("source") or "mercado_pago")[:120],
            campaign=str(metadata.get("campaign") or metadata.get("utm_campaign") or "")[:160] or None,
            funnel=str(metadata.get("funnel") or "LeoFT")[:120] or None,
            status="received",
            current_stage="received",
            event_metadata={"environment": str(environment), "payment_metadata": _safe_metadata(metadata)},
        )
        session.add(sale)
        try:
            await session.flush()
        except IntegrityError:
            await session.rollback()
            existing = await session.scalar(
                select(LeoFTSaleFulfillment).where(
                    LeoFTSaleFulfillment.company_id == company_id,
                    LeoFTSaleFulfillment.payment_provider == provider,
                    LeoFTSaleFulfillment.payment_id == payment_id,
                )
            )
            if existing is None:
                raise
            return _result(existing, duplicate=True)

    sale.status = "processing"
    sale.current_stage = "sale_registered"
    await session.flush()

    try:
        sale.current_stage = "contact_resolved"
        sale.lead_id = lead.id
        if sale.contact_id is None:
            contact = await _resolve_contact(session, lead)
            sale.contact_id = contact.id if contact is not None else None
        student = await _resolve_student(session, lead=lead, anamnesis_id=sale.anamnesis_id)
        sale.student_id = student.id
        sale.current_stage = "student_resolved"
        await session.flush()

        access = await _resolve_access(session, student=student, lead=lead)
        sale.access_id = access["id"]
        sale.current_stage = "access_provisioned"
        await session.flush()

        mapping = await _resolve_offer_mapping(session, company_id=company_id, product_id=sale.product_id)
        if mapping is None or not mapping.active:
            sale.status = "manual_review"
            sale.current_stage = "manual_review"
            sale.last_error_code = "PROGRAM_ASSIGNMENT_PENDING"
            sale.last_error_sanitized = "Oferta confirmada sem template de Programa de Treino configurado."
            sale.post_sale_status = "manual_review"
            await _update_crm(session, lead, sale, stage_name="Venda")
            sale.current_stage = "manual_review"
            await session.flush()
            return _result(sale, duplicate=False)

        product_type = str(mapping.product_type or sale.product_type).strip().upper()
        fulfillment_type = str(mapping.fulfillment_type or sale.fulfillment_type).strip().upper()
        sale.product_type = product_type
        sale.fulfillment_type = fulfillment_type
        sale.training_program_template_id = mapping.training_program_template_id
        if product_type not in PRODUCT_TYPES or fulfillment_type not in FULFILLMENT_TYPES:
            return await _mark_manual_review(
                session, lead, sale, "UNSUPPORTED_FULFILLMENT_TYPE", "Tipo de produto/fulfillment não suportado."
            )

        if fulfillment_type == "PERSONALIZED_PROGRAM_REQUEST":
            request = await _create_personalization_request(
                session, sale=sale, lead=lead, student=student, payment_metadata=metadata
            )
            sale.personalization_request_id = request.id
            sale.status = "partial"
            sale.current_stage = "waiting_for_anamnesis" if request.status == "waiting_for_anamnesis" else "personalization_requested"
            sale.post_sale_status = "pending_personalization"
            sale.last_error_code = "ANAMNESIS_PENDING" if request.status == "waiting_for_anamnesis" else "PERSONALIZATION_PENDING"
            sale.last_error_sanitized = "Personalização aguardando anamnese/produção."[:500]
            await _update_crm(session, lead, sale, stage_name="Venda")
            await session.flush()
            return _result(sale, duplicate=False)

        if fulfillment_type == "MANUAL_SERVICE":
            return await _mark_manual_review(
                session, lead, sale, "MANUAL_SERVICE_PENDING", "Produto requer atendimento manual."
            )

        if fulfillment_type == "ACCESS_ONLY":
            sale.current_stage = "access_provisioned"
        else:
            if mapping.training_program_template_id is None:
                return await _mark_manual_review(
                    session, lead, sale, "PROGRAM_ASSIGNMENT_PENDING", "Fulfillment de programa sem template configurado."
                )
            program_id = await _copy_program_for_student(
                session,
                company_id=company_id,
                template_id=mapping.training_program_template_id,
                student_id=student.id,
                sale_id=sale.id,
            )
            sale.student_program_id = program_id
            sale.current_stage = "program_assigned"
        await session.flush()

        await _update_crm(session, lead, sale, stage_name="Venda")
        sale.current_stage = "crm_updated"
        await session.flush()

        try:
            await _trigger_post_sale(
                session,
                sale=sale,
                lead=lead,
                student=student,
                access=access,
                environment=environment,
            )
        except Exception as exc:
            sale.post_sale_status = "failed"
            sale.last_error_code = "POST_SALE_MESSAGE_FAILED"
            sale.last_error_sanitized = type(exc).__name__
        sale.current_stage = "post_sale_triggered"
        sale.status = "completed" if sale.post_sale_status in {"queued", "skipped", "sent"} else "partial"
        sale.completed_at = datetime.utcnow() if sale.status == "completed" else None
        if sale.status == "partial":
            sale.last_error_code = sale.last_error_code or "POST_SALE_MESSAGE_FAILED"
        await session.flush()
        return _result(sale, duplicate=False)
    except SaleFulfillmentError as exc:
        sale.status = "failed"
        sale.last_error_code = exc.code
        sale.last_error_sanitized = str(exc)[:500]
        sale.current_stage = "manual_review"
        sale.retry_count = int(sale.retry_count or 0) + 1
        await session.flush()
        return _result(sale, duplicate=False)
    except Exception as exc:
        sale.status = "failed"
        sale.last_error_code = type(exc).__name__
        sale.last_error_sanitized = str(exc)[:500] or type(exc).__name__
        sale.current_stage = "manual_review"
        sale.retry_count = int(sale.retry_count or 0) + 1
        await session.flush()
        return _result(sale, duplicate=False)


async def retry_sale_fulfillment(session, fulfillment_id: str) -> dict[str, Any]:
    sale = await session.get(LeoFTSaleFulfillment, uuid.UUID(str(fulfillment_id)), with_for_update=True)
    if sale is None:
        raise SaleFulfillmentError("fulfillment_not_found")
    if sale.status == "completed":
        return _result(sale, duplicate=True)
    lead = await session.get(CRMLead, sale.lead_id) if sale.lead_id else None
    if lead is None:
        raise SaleFulfillmentError("lead_not_found")
    payment = {
        "id": sale.payment_id,
        "status": "approved",
        "provider": sale.payment_provider,
        "external_reference": sale.order_id,
        "transaction_amount": sale.amount,
        "currency_id": sale.currency,
        "date_approved": sale.paid_at.isoformat() if sale.paid_at else None,
        "metadata": dict(sale.event_metadata or {}).get("payment_metadata") or {},
    }
    return await fulfill_sale_confirmed(
        session,
        payment=payment,
        lead=lead,
        anamnese_id=sale.anamnesis_id,
        environment=str((sale.event_metadata or {}).get("environment") or "production"),
        force_retry=True,
    )


async def _mark_manual_review(session, lead: CRMLead, sale: LeoFTSaleFulfillment, code: str, message: str) -> dict[str, Any]:
    sale.status = "manual_review"
    sale.current_stage = "manual_review"
    sale.last_error_code = code
    sale.last_error_sanitized = message[:500]
    sale.post_sale_status = "manual_review"
    await _update_crm(session, lead, sale, stage_name="Venda")
    await session.flush()
    return _result(sale, duplicate=False)


async def _create_personalization_request(
    session,
    *,
    sale: LeoFTSaleFulfillment,
    lead: CRMLead,
    student: LeoFTStudent,
    payment_metadata: Mapping[str, Any],
) -> LeoFTPersonalizationRequest:
    request = await session.scalar(
        select(LeoFTPersonalizationRequest)
        .where(LeoFTPersonalizationRequest.sale_fulfillment_id == sale.id)
        .with_for_update()
    )
    anamnesis_id, analysis_id = await _resolve_personalization_inputs(
        session, sale=sale, lead=lead, payment_metadata=payment_metadata
    )
    if request is None:
        request = LeoFTPersonalizationRequest(
            id=uuid.uuid4(),
            company_id=sale.company_id,
            sale_fulfillment_id=sale.id,
            student_id=student.id,
            anamnesis_id=anamnesis_id,
            analysis_id=analysis_id,
            status="ready_for_creation" if anamnesis_id else "waiting_for_anamnesis",
            event_metadata={"product_type": sale.product_type, "fulfillment_type": sale.fulfillment_type},
        )
        session.add(request)
    else:
        request.anamnesis_id = request.anamnesis_id or anamnesis_id
        request.analysis_id = request.analysis_id or analysis_id
        if request.status == "waiting_for_anamnesis" and request.anamnesis_id:
            request.status = "ready_for_creation"
    await session.flush()
    return request


async def _resolve_personalization_inputs(
    session, *, sale: LeoFTSaleFulfillment, lead: CRMLead, payment_metadata: Mapping[str, Any]
) -> tuple[str | None, str | None]:
    anamnesis_id = str(sale.anamnesis_id or payment_metadata.get("anamnesis_id") or "").strip() or None
    analysis_id = str(payment_metadata.get("analysis_id") or "").strip() or None
    data = lead.dados_adicionais if isinstance(lead.dados_adicionais, dict) else {}
    anamnese = (data.get("anamneses") or {}).get("leoft") if isinstance(data.get("anamneses"), dict) else {}
    if isinstance(anamnese, dict):
        anamnesis_id = anamnesis_id or str(anamnese.get("id") or anamnese.get("anamnese_id") or "").strip() or None
        if anamnesis_id is None and anamnese.get("respostas"):
            anamnesis_id = f"lead:{lead.id}:leoft"
        analysis_id = analysis_id or str(anamnese.get("analysis_id") or anamnese.get("analysis", {}).get("id") if isinstance(anamnese.get("analysis"), dict) else "").strip() or None
    if anamnesis_id is None:
        snapshot = await session.scalar(
            text("SELECT snapshot_id FROM leoft_anamnesis_snapshots WHERE company_id=:company_id AND student_id=:lead_id ORDER BY created_at DESC LIMIT 1"),
            {"company_id": sale.company_id, "lead_id": lead.id},
        )
        anamnesis_id = str(snapshot) if snapshot else None
    if analysis_id is None:
        plan = await session.scalar(
            text("SELECT id FROM leoft_training_plans WHERE empresa_id=:company_id AND lead_id=:lead_id AND status <> 'cancelled' ORDER BY created_at DESC LIMIT 1"),
            {"company_id": sale.company_id, "lead_id": lead.id},
        )
        analysis_id = str(plan) if plan else None
    return anamnesis_id, analysis_id


async def _resolve_student(session, *, lead: CRMLead, anamnesis_id: str | None) -> LeoFTStudent:
    student = await session.scalar(
        select(LeoFTStudent)
        .where(LeoFTStudent.empresa_id == lead.empresa_id, LeoFTStudent.crm_lead_id == lead.id)
        .with_for_update()
    )
    normalized_phone = normalize_whatsapp_address(lead.telefone_contato).e164
    if student is None:
        result = await session.execute(
            select(LeoFTStudent)
            .where(LeoFTStudent.empresa_id == lead.empresa_id, LeoFTStudent.active.is_(True))
            .with_for_update()
        )
        candidates = list(result.scalars().all())
        email = _lead_email(lead)
        student = next((item for item in candidates if email and str(item.email or "").strip().lower() == email), None)
        if student is None and normalized_phone:
            student = next(
                (item for item in candidates if normalize_whatsapp_address(item.whatsapp).e164 == normalized_phone),
                None,
            )
    if student is None:
        student = LeoFTStudent(
            id=uuid.uuid4(),
            empresa_id=lead.empresa_id,
            name=(lead.nome_contato or "Aluno LeoFT").strip()[:160],
            email=_lead_email(lead),
            whatsapp=normalized_phone or (str(lead.telefone_contato or "").strip() or None),
            active=True,
            crm_lead_id=lead.id,
            assessment_id=str(anamnesis_id or "")[:160] or None,
        )
        session.add(student)
    else:
        student.crm_lead_id = student.crm_lead_id or lead.id
        student.name = student.name or (lead.nome_contato or "Aluno LeoFT").strip()[:160]
        student.email = student.email or _lead_email(lead)
        student.whatsapp = student.whatsapp or normalized_phone or str(lead.telefone_contato or "").strip() or None
        student.assessment_id = student.assessment_id or str(anamnesis_id or "")[:160] or None
    await session.flush()
    return student


async def _resolve_contact(session, lead: CRMLead) -> ChannelContact | None:
    return await session.scalar(
        select(ChannelContact)
        .where(ChannelContact.empresa_id == lead.empresa_id, ChannelContact.lead_id == lead.id)
        .order_by(ChannelContact.atualizado_em.desc())
        .limit(1)
    )


async def _resolve_access(session, *, student: LeoFTStudent, lead: CRMLead) -> dict[str, Any]:
    access = await session.scalar(
        select(LeoFTStudentAccess)
        .where(LeoFTStudentAccess.empresa_id == student.empresa_id, LeoFTStudentAccess.student_id == student.id, LeoFTStudentAccess.status == "active")
        .with_for_update()
    )
    if access is not None:
        return {"id": access.id, "app_link": None, "created": False, "token_masked": access.access_token_masked}
    token = "leoft_app_" + __import__("secrets").token_urlsafe(32)
    password = "LeoFT-" + __import__("secrets").token_urlsafe(18)
    app_url = str(os.getenv("LEOFT_STUDENT_APP_URL") or "https://app.leoft.com.br/acesso").rstrip("/")
    login = str(student.email or student.whatsapp or student.id)
    access = LeoFTStudentAccess(
        id=uuid.uuid4(),
        empresa_id=student.empresa_id,
        student_id=student.id,
        lead_id=lead.id,
        plan_id=None,
        student_email=student.email,
        student_whatsapp=student.whatsapp,
        login_identifier=login,
        password_hash=get_password_hash(password),
        temporary_password_masked=f"{password[:4]}...{password[-4:]}",
        access_token_hash=hash_token(token),
        access_token_masked=mask_token(token),
        app_url=app_url,
        status="active",
    )
    session.add(access)
    await session.flush()
    return {
        "id": access.id,
        "app_link": f"{app_url}?token={token}",
        "created": True,
        "token_masked": access.access_token_masked,
    }


async def _resolve_offer_mapping(session, *, company_id: uuid.UUID, product_id: str) -> LeoFTOfferFulfillment | None:
    return await session.scalar(
        select(LeoFTOfferFulfillment).where(
            LeoFTOfferFulfillment.company_id == company_id,
            LeoFTOfferFulfillment.product_id == product_id,
            LeoFTOfferFulfillment.active.is_(True),
        )
    )


async def _copy_program_for_student(session, *, company_id: uuid.UUID, template_id: uuid.UUID, student_id: uuid.UUID, sale_id: uuid.UUID) -> uuid.UUID:
    # The sale row is locked by the caller. The template and every editable
    # workout/prescription are cloned in this same PostgreSQL transaction.
    template = (await session.execute(text("""
        SELECT id, name, description, objective, level, suggested_frequency
        FROM leoft_training_programs
        WHERE id=:template_id AND empresa_id=:company_id AND student_id IS NULL AND status <> 'archived'
        FOR UPDATE
    """), {"template_id": template_id, "company_id": company_id})).mappings().first()
    if not template:
        raise SaleFulfillmentError("program_template_not_found")
    existing = (await session.execute(text("""
        SELECT id FROM leoft_training_programs
        WHERE empresa_id=:company_id AND source_program_id=:template_id AND student_id=:student_id
          AND status='active' AND description LIKE :marker
        LIMIT 1
    """), {"company_id": company_id, "template_id": template_id, "student_id": student_id, "marker": f"%sale:{sale_id}%"})).scalar()
    if existing:
        return uuid.UUID(str(existing))
    program_id = uuid.uuid4()
    await session.execute(text("""
        INSERT INTO leoft_training_programs
          (id, empresa_id, student_id, source_program_id, name, description, objective, level, suggested_frequency, status)
        VALUES (:id, :company_id, :student_id, :template_id, :name, :description, :objective, :level, :frequency, 'active')
    """), {
        "id": program_id, "company_id": company_id, "student_id": student_id, "template_id": template_id,
        "name": f"{template['name']} — aluno", "description": f"{template['description'] or ''}\n\nOrigem de fulfillment sale:{sale_id}",
        "objective": template["objective"], "level": template["level"], "frequency": template["suggested_frequency"],
    })
    sessions = (await session.execute(text("""
        SELECT id, workout_id, session_order, label, name
        FROM leoft_training_program_sessions
        WHERE program_id=:template_id AND empresa_id=:company_id ORDER BY session_order
    """), {"template_id": template_id, "company_id": company_id})).mappings().all()
    for source in sessions:
        workout_id = uuid.uuid4()
        await session.execute(text("""
            INSERT INTO leoft_manual_workouts (id, empresa_id, name, description, modality, active)
            SELECT :new_id, empresa_id, name, description, modality, true
            FROM leoft_manual_workouts WHERE id=:source_id AND empresa_id=:company_id AND active=true
        """), {"new_id": workout_id, "source_id": source["workout_id"], "company_id": company_id})
        await session.execute(text("""
            INSERT INTO leoft_manual_workout_exercises
              (id, workout_id, exercise_id, position, sets, reps, duration_seconds, rest_seconds, load, notes, created_at, updated_at, rir, rpe)
            SELECT gen_random_uuid(), :new_id, exercise_id, position, sets, reps, duration_seconds, rest_seconds, load, notes, now(), now(), rir, rpe
            FROM leoft_manual_workout_exercises WHERE workout_id=:source_id ORDER BY position
        """), {"new_id": workout_id, "source_id": source["workout_id"]})
        await session.execute(text("""
            INSERT INTO leoft_training_program_sessions
              (id, empresa_id, program_id, workout_id, session_order, label, name)
            VALUES (gen_random_uuid(), :company_id, :program_id, :workout_id, :session_order, :label, :name)
        """), {"company_id": company_id, "program_id": program_id, "workout_id": workout_id,
                "session_order": source["session_order"], "label": source["label"], "name": source["name"]})
        assignment_position = await session.scalar(text("""
            SELECT COALESCE(MAX(position), 0) + 1
            FROM leoft_student_workout_assignments
            WHERE empresa_id=:company_id AND student_id=:student_id
        """), {"company_id": company_id, "student_id": student_id})
        await session.execute(text("""
            INSERT INTO leoft_student_workout_assignments
              (id, empresa_id, student_id, workout_id, position, status, released_at, created_at, updated_at)
            VALUES (gen_random_uuid(), :company_id, :student_id, :workout_id, :position, 'released', now(), now(), now())
        """), {"company_id": company_id, "student_id": student_id, "workout_id": workout_id, "position": assignment_position})
    return program_id


async def _update_crm(session, lead: CRMLead, sale: LeoFTSaleFulfillment, *, stage_name: str) -> None:
    stage = await session.scalar(
        select(CRMEtapa)
        .join(CRMFunil, CRMEtapa.funil_id == CRMFunil.id)
        .where(CRMFunil.empresa_id == lead.empresa_id, CRMEtapa.nome.ilike(stage_name))
        .order_by(CRMEtapa.ordem)
        .limit(1)
    )
    if stage is not None:
        lead.etapa_id = stage.id
    tag = await session.scalar(
        select(TagCRM).where(TagCRM.empresa_id == lead.empresa_id, TagCRM.nome.ilike("LeoFT - Pagamento confirmado"))
    )
    if tag is not None:
        tags = list(lead.tags or []) if isinstance(lead.tags, list) else []
        if str(tag.id) not in {str(item) for item in tags}:
            tags.append(str(tag.id))
            lead.tags = tags
            flag_modified(lead, "tags")
    data = dict(lead.dados_adicionais or {}) if isinstance(lead.dados_adicionais, dict) else {}
    commercial = dict(data.get("commercial_state") or {})
    commercial.update({"state": "customer", "sale_id": str(sale.id), "payment_id": sale.payment_id, "fulfillment_status": sale.status, "updated_at": datetime.utcnow().isoformat()})
    data["commercial_state"] = commercial
    data["sale_fulfillment_id"] = str(sale.id)
    lead.dados_adicionais = data
    flag_modified(lead, "dados_adicionais")


async def _trigger_post_sale(session, *, sale: LeoFTSaleFulfillment, lead: CRMLead, student: LeoFTStudent, access: dict[str, Any], environment: str) -> None:
    if environment in {"e2e", "staging"}:
        sale.post_sale_status = "skipped"
        return
    if not access.get("app_link"):
        sale.post_sale_status = "skipped"
        return
    contact = await session.scalar(
        select(ChannelContact).where(ChannelContact.empresa_id == lead.empresa_id, ChannelContact.lead_id == lead.id).order_by(ChannelContact.atualizado_em.desc()).limit(1)
    )
    if contact is None or contact.conexao_id is None:
        sale.post_sale_status = "manual_review"
        sale.last_error_code = "POST_SALE_CONTACT_UNAVAILABLE"
        return
    payload = {
        "kind": "leoft_sale_fulfillment",
        "sale_id": str(sale.id),
        "product_id": sale.product_id,
        "student_id": str(student.id),
        "app_link": access["app_link"],
        "message": "Seu acesso ao Leo APP foi liberado. Abra o link para acessar seu programa de treino.",
    }
    await enqueue_message(
        session,
        empresa_id=lead.empresa_id,
        conexao_id=contact.conexao_id,
        provider=contact.provider,
        channel=contact.channel,
        operation_type="leoft_sale_fulfillment",
        payload=payload,
        idempotency_key=deterministic_idempotency_key("leoft-sale", sale.id, "post-sale"),
        priority=20,
    )
    sale.post_sale_status = "queued"


def _product_id(payment: Mapping[str, Any], metadata: Mapping[str, Any]) -> str:
    value = str(metadata.get("product_id") or metadata.get("offer_code") or "").strip()
    return value[:160] or DEFAULT_PRODUCT_ID


def _lead_email(lead: CRMLead) -> str | None:
    data = lead.dados_adicionais if isinstance(lead.dados_adicionais, dict) else {}
    for value in (data.get("email"), (data.get("anamneses") or {}).get("leoft", {}).get("respostas", {}).get("personalData", {}).get("email")):
        cleaned = str(value or "").strip().lower()
        if cleaned:
            return cleaned[:320]
    return None


def _payment_datetime(payment: Mapping[str, Any]) -> datetime:
    raw = payment.get("date_approved") or payment.get("date_created") or payment.get("date_last_updated")
    if raw:
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _decimal_or_none(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _required(value: Any, code: str) -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        raise SaleFulfillmentError(code)
    return cleaned


def _safe_metadata(value: Mapping[str, Any]) -> dict[str, Any]:
    excluded = {"access_token", "token", "password", "secret", "api_key"}
    result = {}
    for key, item in value.items():
        if str(key).lower() in excluded:
            continue
        if isinstance(item, (str, int, float, bool)) or item is None:
            result[str(key)[:80]] = item
    return result


def _result(sale: LeoFTSaleFulfillment, *, duplicate: bool) -> dict[str, Any]:
    return {
        "ok": sale.status in {"completed", "partial", "manual_review"},
        "duplicate": duplicate,
        "fulfillment_id": str(sale.id),
        "event_id": str(sale.event_id),
        "status": sale.status,
        "stage": sale.current_stage,
        "payment_id": sale.payment_id,
        "lead_id": str(sale.lead_id) if sale.lead_id else None,
        "student_id": str(sale.student_id) if sale.student_id else None,
        "access_id": str(sale.access_id) if sale.access_id else None,
        "student_program_id": str(sale.student_program_id) if sale.student_program_id else None,
        "template_id": str(sale.training_program_template_id) if sale.training_program_template_id else None,
        "post_sale_status": sale.post_sale_status,
        "error_code": sale.last_error_code,
        "error_detail": sale.last_error_sanitized,
    }

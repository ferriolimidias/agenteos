from __future__ import annotations
import hashlib, json, logging, secrets, uuid
from datetime import datetime
from typing import Any, Literal
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.services.leoft_internal_auth import require_hermes_catalog_hmac, require_hermes_hmac
from app.services.leoft_security import hash_token, mask_token
from app.services.leoft_student_app_access_service import build_student_app_link
from app.services.leoft_postgres_central import create_or_get_review, normalize_plan
from app.services.leoft_training_approval_service import approve_training_plan_in_session
from db.database import get_db
from db.models import LeoFTStudentAccess, LeoFTTrainingPlan, LeoFTTrainingPlanVersion, LeoFTTrainingReviewSession, LeoFTTrainingReviewEvent, LeoFTIntegrationEvent
from db.leoft_models import LeoFTPersonalizationRequest, LeoFTSaleFulfillment
from app.services.leoft_sale_fulfillment_service import retry_sale_fulfillment
from db.leoft_models import LeoFTOfferFulfillment

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/internal/hermes/leoft", tags=["LeoFT Hermes Internal"])

VALID_STUDENT_ACCESS_STATUSES = {"active"}

class ReviewCreate(BaseModel):
    plan_id: uuid.UUID
    idempotency_key: str = Field(min_length=8, max_length=256)
    review_public_id: str | None = Field(default=None, min_length=8, max_length=256)
    current_page: int | None = Field(default=None, ge=0)
    max_page_seen: int | None = Field(default=None, ge=0)
    total_pages: int | None = Field(default=None, ge=1)
    total_workouts: int | None = Field(default=None, ge=1)
    telegram_chat_id: str | int | None = None
    telegram_message_id: str | int | None = None
    telegram_user_id: str | int | None = None
    actor_user_id: str | int | None = None
    reviewer_identifier: str | int | None = None
    actor: str | int | None = None
    status: Literal["pending", "reviewing", "awaiting_confirmation", "failed"] | None = None
    expected_lock: int | None = Field(default=None, ge=0)

    @field_validator("review_public_id")
    @classmethod
    def _validate_public_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        text = value.strip()
        if not text:
            return None
        allowed = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-")
        if any(char not in allowed for char in text):
            raise ValueError("invalid_review_public_id")
        return text

    @field_validator("telegram_chat_id", "telegram_message_id", "telegram_user_id", "actor_user_id", "reviewer_identifier", "actor", mode="before")
    @classmethod
    def _normalize_textish(cls, value: Any) -> str | None:
        if value is None:
            return None
        if isinstance(value, (str, int)):
            text = str(value).strip()
            return text or None
        raise ValueError("invalid_text_field")

class Navigate(BaseModel):
    action: Literal["next", "previous"]; expected_lock: int = Field(ge=0); callback_id: str = Field(min_length=1, max_length=256); actor: str = Field(min_length=1, max_length=256)
class ActorBody(BaseModel):
    actor: str = Field(min_length=1, max_length=256); callback_id: str | None = Field(default=None, max_length=256)
class LegacyApproveBody(ActorBody):
    review_public_id: str = Field(min_length=8, max_length=256)

class RotateStudentAccessTokenBody(BaseModel):
    actor: str = Field(min_length=1, max_length=256)
    reason: Literal["administrative_reissue"]


async def _catalog_auth(request: Request):
    return await require_hermes_catalog_hmac(request)


def _catalog_response(document: dict[str, Any], counters: dict[str, int], *, dry_run: bool, idempotent: bool, import_run_id: str | None = None) -> dict[str, Any]:
    response = {
        "ok": True,
        "event_type": "leoft.catalog.import",
        "schema_version": "1.0",
        "catalog_version": document["version"],
        "source_sha256": document["source_sha256"],
        "dry_run": dry_run,
        "idempotent": idempotent,
        "counts": counters,
    }
    if import_run_id:
        response["import_run_id"] = import_run_id
    return response

async def _auth(request: Request = None): return await require_hermes_hmac(request)
async def _review(db, public_id):
    row = await db.scalar(select(LeoFTTrainingReviewSession).where(LeoFTTrainingReviewSession.review_public_id == public_id))
    if not row: raise HTTPException(404, "review_not_found")
    return row

async def _active_student_access(db: AsyncSession, plan_id):
    return await db.scalar(
        select(LeoFTStudentAccess)
        .where(
            LeoFTStudentAccess.plan_id == plan_id,
            LeoFTStudentAccess.status.in_(VALID_STUDENT_ACCESS_STATUSES),
            LeoFTStudentAccess.revoked_at.is_(None),
        )
        .order_by(LeoFTStudentAccess.created_at.asc())
    )

async def _build_review_state_response(db: AsyncSession, review: LeoFTTrainingReviewSession, *, idempotent: bool | None = None) -> dict[str, Any]:
    plan = await db.get(LeoFTTrainingPlan, review.plan_id)
    if plan is None:
        raise HTTPException(
            status_code=409,
            detail={
                "ok": False,
                "code": "linked_plan_not_found",
                "review_public_id": review.review_public_id,
                "plan_id": str(review.plan_id),
            },
        )
    access = await _active_student_access(db, review.plan_id)
    response = {
        "ok": True,
        "review_public_id": review.review_public_id,
        "plan_id": str(review.plan_id),
        "version_id": str(review.version_id),
        "status": review.status,
        "review_status": review.status,
        "plan_status": plan.status,
        "current_page": review.current_page,
        "total_pages": review.total_pages,
        "optimistic_lock": review.optimistic_lock,
        "telegram_message_id": review.telegram_message_id,
        "student_access_exists": access is not None,
        "student_access_status": access.status if access is not None else None,
        "approved_at": review.approved_at.isoformat() if review.approved_at else None,
    }
    if idempotent is not None:
        response["idempotent"] = idempotent
    return response


@router.post("/catalog/import")
async def import_catalog_from_hermes(
    request: Request,
    dry_run: bool = False,
    auth=Depends(_catalog_auth),
    db: AsyncSession = Depends(get_db),
):
    from scripts.import_leoft_catalog import validate_catalog_document, import_catalog_document

    idempotency_key, request_hash = auth
    raw_body = await request.body()
    try:
        payload = json.loads(raw_body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(422, "invalid_catalog_json")
    if not isinstance(payload, dict):
        raise HTTPException(422, "catalog_payload_must_be_object")

    existing = await db.scalar(select(LeoFTIntegrationEvent).where(
        LeoFTIntegrationEvent.integration == "leoft_catalog_import",
        LeoFTIntegrationEvent.idempotency_key == idempotency_key,
    ))
    if existing is not None:
        existing_request_hash = existing.request_hash
        stored = dict(existing.response_payload_sanitized or {"ok": True})
        await db.rollback()
        if existing_request_hash != request_hash:
            raise HTTPException(409, "idempotency_key_request_hash_mismatch")
        stored["idempotent"] = True
        return stored

    try:
        counters = validate_catalog_document(payload, enforce_envelope=True)
    except (TypeError, ValueError, KeyError) as exc:
        await db.rollback()
        raise HTTPException(422, "invalid_catalog_contract") from exc

    if dry_run:
        response = _catalog_response(payload, counters, dry_run=True, idempotent=False)
        db.add(LeoFTIntegrationEvent(
            direction="inbound",
            integration="leoft_catalog_import",
            event_type="leoft.catalog.import.dry_run",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            request_payload_sanitized={"event_type": payload["event_type"], "catalog_version": payload["version"], "source_sha256": payload["source_sha256"], "counts": response["counts"], "dry_run": True},
            response_status=200,
            response_payload_sanitized=response,
            status="dry_run",
            processed_at=datetime.utcnow(),
        ))
        await db.commit()
        return response

    try:
        result = await import_catalog_document(
            payload,
            payload["source_sha256"],
            db,
            source_filename="hermes:catalog_payload",
        )
        response = _catalog_response(payload, counters, dry_run=False, idempotent=bool(result.get("idempotent")), import_run_id=result.get("import_run_id"))
        db.add(LeoFTIntegrationEvent(
            direction="inbound",
            integration="leoft_catalog_import",
            event_type="leoft.catalog.import",
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            request_payload_sanitized={"event_type": payload["event_type"], "catalog_version": payload["version"], "source_sha256": payload["source_sha256"], "counts": response["counts"]},
            response_status=200,
            response_payload_sanitized=response,
            status="processed",
            processed_at=datetime.utcnow(),
        ))
        await db.commit()
        return response
    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await db.rollback()
        logger.exception("leoft_catalog_import_failed code=catalog_import_transaction_failed exc_type=%s", type(exc).__name__)
        raise HTTPException(422, "catalog_import_transaction_failed") from exc


from app.api.routers.leoft_catalog_read import router as catalog_read_router
from app.api.routers.leoft_generation_internal import router as generation_router

router.include_router(catalog_read_router)
router.include_router(generation_router)

@router.post("/reviews")
async def create_review(body: ReviewCreate, auth=Depends(_auth), db: AsyncSession=Depends(get_db)):
    correlation_id = str(uuid.uuid4())
    try:
        plan = await db.get(LeoFTTrainingPlan, body.plan_id)
        if not plan:
            raise HTTPException(404, "plan_not_found")
        normalized = await normalize_plan(db, plan, source="review_api", created_by="hermes")
        review = await create_or_get_review(
            db,
            plan,
            version_id=uuid.UUID(normalized["version_id"]),
            payload=body.model_dump(),
            idempotency_key=body.idempotency_key,
        )
        await db.commit()
        return {
            "ok": True,
            "review_public_id": review.review_public_id,
            "plan_id": str(review.plan_id),
            "version_id": str(review.version_id),
            "status": review.status,
            "current_page": review.current_page,
            "max_page_seen": review.current_page,
            "total_pages": review.total_pages,
            "total_workouts": review.total_pages,
            "optimistic_lock": review.optimistic_lock,
            "telegram_message_id": review.telegram_message_id,
            "correlation_id": correlation_id,
        }
    except HTTPException:
        await db.rollback()
        raise
    except Exception as exc:
        await db.rollback()
        logger.exception(
            "leoft_review_create_failed correlation_id=%s code=%s exc_type=%s plan_id_prefix=%s review_public_id=%s",
            correlation_id,
            "review_create_internal_error",
            type(exc).__name__,
            str(body.plan_id)[:8],
            body.review_public_id,
        )
        return JSONResponse(
            status_code=500,
            content={
                "status": "error",
                "code": "review_create_internal_error",
                "message": "Falha interna ao criar ou recuperar revisao LeoFT.",
                "retryable": True,
                "correlation_id": correlation_id,
            },
        )

@router.get("/reviews/{review_public_id}")
async def get_review(review_public_id: str, auth=Depends(_auth), db: AsyncSession=Depends(get_db)):
    r = await _review(db, review_public_id)
    return await _build_review_state_response(db, r)

@router.get("/reviews/{review_public_id}/pages/{page}")
async def get_page(review_public_id: str, page: int, auth=Depends(_auth), db: AsyncSession=Depends(get_db)):
    r = await _review(db, review_public_id)
    if page < 0 or page >= r.total_pages: raise HTTPException(422, "page_out_of_range")
    v = await db.get(LeoFTTrainingPlanVersion, r.version_id)
    weeks = (v.snapshot_json or {}).get("weeks") or []
    if not weeks and (v.snapshot_json or {}).get("months"):
        weeks = [w for m in v.snapshot_json["months"] for w in (m.get("weeks") or [])]
    return {"ok": True, "review_public_id": r.review_public_id, "page": page, "total_pages": r.total_pages, "content": weeks[page] if page < len(weeks) else {}}

@router.post("/reviews/{review_public_id}/rotate-student-access-token")
async def rotate_student_access_token(
    review_public_id: str,
    body: RotateStudentAccessTokenBody,
    auth=Depends(_auth),
    db: AsyncSession = Depends(get_db),
):
    idempotency_key = auth[0]
    existing = await db.scalar(select(LeoFTIntegrationEvent).where(
        LeoFTIntegrationEvent.integration == "leoft_student_access_token_rotation",
        LeoFTIntegrationEvent.idempotency_key == idempotency_key,
    ))
    if existing is not None:
        raise HTTPException(409, "idempotency_key_already_processed")

    review = await _review(db, review_public_id)
    plan = await db.scalar(select(LeoFTTrainingPlan).where(LeoFTTrainingPlan.id == review.plan_id).with_for_update())
    if plan is None:
        raise HTTPException(404, "plan_not_found")
    if review.status != "approved":
        raise HTTPException(409, "review_not_approved")
    if plan.status != "approved":
        raise HTTPException(409, "plan_not_approved")

    accesses = list((await db.scalars(select(LeoFTStudentAccess).where(
        LeoFTStudentAccess.plan_id == plan.id,
        LeoFTStudentAccess.status == "active",
    ).with_for_update())).all())
    if not accesses:
        raise HTTPException(404, "student_access_not_found")
    if len(accesses) != 1:
        raise HTTPException(409, "multiple_active_student_accesses")
    access = accesses[0]
    if access.revoked_at is not None:
        raise HTTPException(409, "student_access_revoked")

    access_token = "leoft_app_" + secrets.token_urlsafe(32)
    app_url = await _resolve_rotation_app_url(db, plan)
    access.access_token_hash = hash_token(access_token)
    access.access_token_masked = mask_token(access_token)
    access.app_url = app_url
    access.updated_at = datetime.utcnow()
    db.add(LeoFTIntegrationEvent(
        direction="internal",
        integration="leoft_student_access_token_rotation",
        event_type="student_access_token_rotated",
        idempotency_key=idempotency_key,
        plan_id=plan.id,
        review_session_id=review.id,
        request_hash=auth[1],
        request_payload_sanitized={"actor": body.actor, "reason": body.reason, "review_public_id": review_public_id},
        response_status=200,
        response_payload_sanitized={"ok": True, "status": "rotated", "access_id": str(access.id), "app_url": app_url},
        status="processed",
        processed_at=datetime.utcnow(),
    ))
    await db.commit()
    return {
        "ok": True,
        "status": "rotated",
        "review_public_id": review_public_id,
        "plan_id": str(plan.id),
        "access": {
            "id": str(access.id),
            "status": access.status,
            "app_url": app_url,
            "access_token": access_token,
            "access_token_masked": access.access_token_masked,
            "app_link": build_student_app_link(access_token, app_url=app_url),
            "app_link_masked": build_student_app_link(access.access_token_masked, app_url=app_url),
        },
    }

async def _resolve_rotation_app_url(db: AsyncSession, plan: LeoFTTrainingPlan) -> str:
    from app.services.leoft_student_app_access_service import _resolve_app_url
    return await _resolve_app_url(db, plan)

@router.post("/reviews/{review_public_id}/navigate")
async def navigate(review_public_id: str, body: Navigate, auth=Depends(_auth), db: AsyncSession=Depends(get_db)):
    r = await _review(db, review_public_id)
    if r.optimistic_lock != body.expected_lock: raise HTTPException(409, "optimistic_lock_conflict")
    before = r.current_page; target = before + (1 if body.action == "next" else -1)
    if target < 0 or target >= r.total_pages: raise HTTPException(422, "page_out_of_range")
    r.current_page = target; r.optimistic_lock += 1; r.status = "reviewing"; r.last_callback_id_hash = hashlib.sha256(body.callback_id.encode()).hexdigest()
    db.add(LeoFTTrainingReviewEvent(review_session_id=r.id, plan_id=r.plan_id, event_type=f"navigate_{body.action}", page_before=before, page_after=target, actor_type="hermes", actor_identifier=body.actor, callback_id_hash=r.last_callback_id_hash, idempotency_key=auth[0], payload_sanitized={"action": body.action}, result_status="ok")); await db.commit()
    return {"ok": True, "current_page": target, "optimistic_lock": r.optimistic_lock}

@router.post("/reviews/{review_public_id}/request-confirmation")
async def request_confirmation(review_public_id: str, body: ActorBody, auth=Depends(_auth), db: AsyncSession=Depends(get_db)):
    r = await _review(db, review_public_id); r.status = "awaiting_confirmation"; r.confirmation_requested_at = datetime.utcnow(); r.optimistic_lock += 1
    db.add(LeoFTTrainingReviewEvent(review_session_id=r.id, plan_id=r.plan_id, event_type="confirmation_requested", actor_type="hermes", actor_identifier=body.actor, idempotency_key=auth[0], payload_sanitized={}, result_status="ok")); await db.commit(); return {"ok": True, "status": r.status, "optimistic_lock": r.optimistic_lock}

@router.post("/reviews/{review_public_id}/approve")
async def approve(review_public_id: str, body: ActorBody, auth=Depends(_auth), db: AsyncSession=Depends(get_db)):
    r = await _review(db, review_public_id)
    plan = await db.scalar(select(LeoFTTrainingPlan).where(LeoFTTrainingPlan.id == r.plan_id).with_for_update())
    if plan is None:
        raise HTTPException(
            status_code=409,
            detail={
                "ok": False,
                "code": "linked_plan_not_found",
                "review_public_id": r.review_public_id,
                "plan_id": str(r.plan_id),
            },
        )
    if r.status == "approved" and plan.status == "approved":
        access = await _active_student_access(db, plan.id)
        if access is None:
            logger.error(
                "leoft_review_approved_without_student_access review_public_id=%s plan_id=%s",
                r.review_public_id,
                str(plan.id),
            )
            raise HTTPException(
                status_code=409,
                detail={
                    "ok": False,
                    "code": "approved_state_missing_student_access",
                    "review_public_id": r.review_public_id,
                    "plan_id": str(plan.id),
                    "status": r.status,
                    "review_status": r.status,
                    "plan_status": plan.status,
                    "student_access_exists": False,
                    "student_access_status": None,
                    "approved_at": r.approved_at.isoformat() if r.approved_at else None,
                },
            )
        return await _build_review_state_response(db, r, idempotent=True)
    if r.status not in {"pending", "reviewing", "awaiting_confirmation", "failed"} or plan.status not in {"pending_approval", "revision_requested"}: raise HTTPException(409, "approval_not_allowed")
    r.status = "approving"
    try:
        await approve_training_plan_in_session(db, plan, approved_by=body.actor, notes="Aprovado via revisão Hermes")
        r.status = "approved"; r.approved_at = datetime.utcnow(); r.approved_by = body.actor; r.optimistic_lock += 1
        db.add(LeoFTTrainingReviewEvent(review_session_id=r.id, plan_id=r.plan_id, event_type="approved", actor_type="hermes", actor_identifier=body.actor, idempotency_key=auth[0], payload_sanitized={}, result_status="ok")); await db.commit(); return await _build_review_state_response(db, r, idempotent=False)
    except Exception as exc:
        await db.rollback(); r = await _review(db, review_public_id); r.status = "failed"; r.failed_at = datetime.utcnow(); r.failure_code = "approval_transaction_failed"; r.failure_message_sanitized = type(exc).__name__; await db.commit(); raise HTTPException(503, "approval_transaction_failed")

@router.post("/reviews/{review_public_id}/events")
async def event(review_public_id: str, body: ActorBody, request: Request, auth=Depends(_auth), db: AsyncSession=Depends(get_db)):
    r = await _review(db, review_public_id); db.add(LeoFTTrainingReviewEvent(review_session_id=r.id, plan_id=r.plan_id, event_type="external_event", actor_type="hermes", actor_identifier=body.actor, callback_id_hash=hashlib.sha256((body.callback_id or "").encode()).hexdigest() if body.callback_id else None, idempotency_key=auth[0], payload_sanitized={}, result_status="recorded")); await db.commit(); return {"ok": True, "status": "recorded"}

# Legacy callback compatibility: old Hermes deployments can use this exact internal path.
@router.post("/events/leoft/training-review/{review_public_id}/approve")
async def legacy_approve(review_public_id: str, body: ActorBody, auth=Depends(_auth), db: AsyncSession=Depends(get_db)):
    return await approve(review_public_id, body, auth, db)

@router.post("/events/leoft/training-review/approve")
async def legacy_approve_without_path(body: LegacyApproveBody, auth=Depends(_auth), db: AsyncSession=Depends(get_db)):
    return await approve(body.review_public_id, body, auth, db)

@router.post("/events/leoft/approve")
async def legacy_approve_short(body: LegacyApproveBody, auth=Depends(_auth), db: AsyncSession=Depends(get_db)):
    return await approve(body.review_public_id, body, auth, db)


class FulfillmentRetryBody(BaseModel):
    actor: str = Field(min_length=1, max_length=256)


class FulfillmentConfigBody(BaseModel):
    company_id: uuid.UUID
    product_id: str = Field(min_length=1, max_length=160)
    offer_code: str = Field(min_length=1, max_length=160)
    product_type: str = Field(default="PERSONALIZED_PROGRAM", min_length=1, max_length=80)
    fulfillment_type: str = Field(default="PERSONALIZED_PROGRAM_REQUEST", min_length=1, max_length=80)
    training_program_template_id: uuid.UUID | None = None
    active: bool = True


@router.get("/fulfillments/{fulfillment_id}")
async def fulfillment_state(fulfillment_id: uuid.UUID, auth=Depends(_auth), db: AsyncSession = Depends(get_db)):
    row = await db.get(LeoFTSaleFulfillment, fulfillment_id)
    if row is None:
        raise HTTPException(404, "fulfillment_not_found")
    personalization = None
    if row.personalization_request_id:
        personalization = await db.get(LeoFTPersonalizationRequest, row.personalization_request_id)
    return {
        "ok": True,
        "fulfillment_id": str(row.id),
        "event_id": str(row.event_id),
        "payment_id": row.payment_id,
        "status": row.status,
        "stage": row.current_stage,
        "student_id": str(row.student_id) if row.student_id else None,
        "access_id": str(row.access_id) if row.access_id else None,
        "student_program_id": str(row.student_program_id) if row.student_program_id else None,
        "product_type": row.product_type,
        "fulfillment_type": row.fulfillment_type,
        "personalization_request_id": str(row.personalization_request_id) if row.personalization_request_id else None,
        "personalization_status": personalization.status if personalization else None,
        "post_sale_status": row.post_sale_status,
        "last_error_code": row.last_error_code,
    }


@router.post("/fulfillments/{fulfillment_id}/retry")
async def fulfillment_retry(
    fulfillment_id: uuid.UUID,
    body: FulfillmentRetryBody,
    auth=Depends(_auth),
    db: AsyncSession = Depends(get_db),
):
    result = await retry_sale_fulfillment(db, str(fulfillment_id))
    await db.commit()
    return {"ok": True, "actor": body.actor, **result}


@router.post("/fulfillment-config")
async def upsert_fulfillment_config(
    body: FulfillmentConfigBody,
    auth=Depends(_auth),
    db: AsyncSession = Depends(get_db),
):
    allowed_product_types = {"APP_ACCESS", "STANDARD_PROGRAM", "PERSONALIZED_PROGRAM", "COACHING"}
    allowed_fulfillment_types = {"ACCESS_ONLY", "STANDARD_PROGRAM_COPY", "PERSONALIZED_PROGRAM_REQUEST", "MANUAL_SERVICE", "PROGRAM_AND_APP"}
    if body.product_type.upper() not in allowed_product_types or body.fulfillment_type.upper() not in allowed_fulfillment_types:
        raise HTTPException(422, "unsupported_product_or_fulfillment_type")
    if body.fulfillment_type.upper() in {"STANDARD_PROGRAM_COPY", "PROGRAM_AND_APP"} and body.training_program_template_id is None:
        raise HTTPException(422, "standard_program_template_required")
    if body.fulfillment_type.upper() == "PERSONALIZED_PROGRAM_REQUEST" and body.training_program_template_id is not None:
        raise HTTPException(422, "personalized_program_must_not_have_template")
    if body.training_program_template_id is not None:
        template = (
            await db.execute(
                text(
                    "SELECT 1 FROM leoft_training_programs WHERE id=:id AND empresa_id=:company_id AND student_id IS NULL AND status <> 'archived'"
                ),
                {"id": body.training_program_template_id, "company_id": body.company_id},
            )
        ).first()
        if template is None:
            raise HTTPException(422, "training_program_template_not_found")
    row = await db.scalar(
        select(LeoFTOfferFulfillment).where(
            LeoFTOfferFulfillment.company_id == body.company_id,
            LeoFTOfferFulfillment.product_id == body.product_id,
        )
    )
    if row is None:
        row = LeoFTOfferFulfillment(company_id=body.company_id, product_id=body.product_id)
        db.add(row)
    row.offer_code = body.offer_code
    row.product_type = body.product_type
    row.fulfillment_type = body.fulfillment_type
    row.training_program_template_id = body.training_program_template_id
    row.active = body.active
    await db.commit()
    return {
        "ok": True,
        "id": str(row.id),
        "company_id": str(row.company_id),
        "product_id": row.product_id,
        "training_program_template_id": str(row.training_program_template_id) if row.training_program_template_id else None,
        "active": row.active,
    }

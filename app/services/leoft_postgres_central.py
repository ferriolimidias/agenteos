from __future__ import annotations

import copy, hashlib, json, re, uuid
from datetime import datetime
from typing import Any
from sqlalchemy import select, func
from db.models import LeoFTTrainingPlan, LeoFTTrainingPlanVersion, LeoFTTrainingWeek, LeoFTTrainingSession, LeoFTTrainingExercise, LeoFTTrainingReviewSession, LeoFTTrainingReviewEvent

REVIEW_PUBLIC_ID_RE = re.compile(r"^[A-Za-z0-9_-]{8,256}$")
REVIEW_STATUSES = {"pending", "reviewing", "awaiting_confirmation", "failed"}

def canonical_checksum(value: dict[str, Any]) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()

def _text(item: Any, *keys: str) -> str | None:
    if not isinstance(item, dict): return None
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip(): return str(value).strip()
    return None

def _items(plan: dict[str, Any]) -> list[dict[str, Any]]:
    direct = plan.get("weeks")
    if isinstance(direct, list): return [x for x in direct if isinstance(x, dict)]
    result = []
    for month in plan.get("months") or []:
        if isinstance(month, dict): result.extend(x for x in (month.get("weeks") or []) if isinstance(x, dict))
    return result

def _sessions(week: dict[str, Any]) -> list[dict[str, Any]]:
    return [x for x in (week.get("sessions") or week.get("workouts") or []) if isinstance(x, dict)]

def _exercises(session: dict[str, Any]) -> list[dict[str, Any]]:
    return [x for x in (session.get("exercises") or []) if isinstance(x, dict)]

def _page_count(plan: dict[str, Any]) -> int:
    return max(1, len(_items(plan)))

def _workout_count(plan: dict[str, Any]) -> int:
    return sum(len(_sessions(week)) for week in _items(plan))

def _positive_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 1 else None

def _non_negative_int(value: Any) -> int | None:
    try:
        result = int(value)
    except (TypeError, ValueError):
        return None
    return result if result >= 0 else None

def _safe_review_public_id(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if not REVIEW_PUBLIC_ID_RE.fullmatch(text):
        raise ValueError("invalid_review_public_id")
    return text

def _review_total_pages(plan_json: dict[str, Any], payload: dict[str, Any]) -> int:
    requested = _positive_int(payload.get("total_pages"))
    if requested is not None:
        return requested
    total_workouts = _positive_int(payload.get("total_workouts"))
    if total_workouts is not None:
        return total_workouts
    workouts = _workout_count(plan_json)
    return workouts or _page_count(plan_json)

def _review_current_page(payload: dict[str, Any], total_pages: int) -> int:
    current = _non_negative_int(payload.get("current_page"))
    max_seen = _non_negative_int(payload.get("max_page_seen"))
    values = [value for value in (current, max_seen) if value is not None]
    if not values:
        return 0
    return min(max(values), total_pages - 1)

def _review_status(payload: dict[str, Any]) -> str:
    requested = str(payload.get("status") or "").strip()
    if requested in REVIEW_STATUSES:
        return requested
    return "reviewing"

async def normalize_plan(session, plan: LeoFTTrainingPlan, *, source: str = "backfill", created_by: str | None = None) -> dict[str, Any]:
    snapshot = copy.deepcopy(dict(plan.plan_json or {}))
    if not snapshot or not isinstance(snapshot, dict): raise ValueError("plan_json deve ser objeto JSON")
    checksum = canonical_checksum(snapshot)
    current = await session.scalar(select(LeoFTTrainingPlanVersion).where(LeoFTTrainingPlanVersion.plan_id == plan.id, LeoFTTrainingPlanVersion.checksum == checksum))
    if current:
        return {"version_id": str(current.id), "version_number": current.version_number, "created": False, "weeks": await session.scalar(select(func.count(LeoFTTrainingWeek.id)).where(LeoFTTrainingWeek.version_id == current.id)), "sessions": await session.scalar(select(func.count(LeoFTTrainingSession.id)).where(LeoFTTrainingSession.version_id == current.id)), "exercises": await session.scalar(select(func.count(LeoFTTrainingExercise.id)).where(LeoFTTrainingExercise.version_id == current.id))}
    last = await session.scalar(select(LeoFTTrainingPlanVersion).where(LeoFTTrainingPlanVersion.plan_id == plan.id).order_by(LeoFTTrainingPlanVersion.version_number.desc()))
    number = (last.version_number + 1) if last else 1
    now = datetime.utcnow()
    version = LeoFTTrainingPlanVersion(plan_id=plan.id, version_number=number, schema_version=_text(snapshot, "schemaVersion", "schema_version"), source=source, status=str(snapshot.get("status") or plan.status), snapshot_json=snapshot, checksum=checksum, created_by=created_by)
    session.add(version); await session.flush()
    if last: last.superseded_at = now
    week_rows, session_rows, exercise_rows = [], [], []
    for wi, week in enumerate(_items(snapshot)):
        wn = int(week.get("week_number") or week.get("weekNumber") or wi + 1)
        if wn <= 0: raise ValueError("week_number deve ser positivo")
        row = LeoFTTrainingWeek(plan_id=plan.id, version_id=version.id, week_number=wn, phase=_text(week, "phase", "fase"), focus=_text(week, "focus", "foco"), position=wi, source_json=week)
        session.add(row); await session.flush(); week_rows.append(row)
        for si, item in enumerate(_sessions(week)):
            external_id = _text(item, "session_id", "sessionId", "id", "workout_id", "workoutId") or f"week-{wn}-session-{si+1}"
            srow = LeoFTTrainingSession(plan_id=plan.id, version_id=version.id, week_id=row.id, external_session_id=external_id, position=si, day_label=_text(item, "day_label", "dayLabel", "day", "dia"), title=_text(item, "title", "name", "nome"), session_type=_text(item, "session_type", "sessionType", "type"), warmup=_text(item, "warmup", "aquecimento"), cooldown=_text(item, "cooldown", "volta_a_calma"), estimated_duration=_text(item, "estimated_duration", "estimatedDuration", "duration"), notes=item.get("notes"), source_json=item)
            session.add(srow); await session.flush(); session_rows.append(srow)
            for ei, exercise in enumerate(_exercises(item)):
                name = _text(exercise, "name", "nome") or f"Exercise {ei+1}"
                erow = LeoFTTrainingExercise(plan_id=plan.id, version_id=version.id, session_id=srow.id, external_exercise_id=_text(exercise, "exercise_id", "exerciseId", "id") or f"{external_id}-exercise-{ei+1}", position=ei, name=name, muscle_group=_text(exercise, "muscle_group", "muscleGroup"), sets=_int(exercise.get("sets")), reps=_text(exercise, "reps"), duration=_text(exercise, "duration"), rest=_text(exercise, "rest"), intensity=_text(exercise, "intensity"), instructions=_text(exercise, "instructions", "instruction"), gif_url=_text(exercise, "gif_url", "gifUrl"), alternatives=exercise.get("alternatives"), caution_tags=exercise.get("caution_tags") or exercise.get("cautionTags"), source_json=exercise)
                session.add(erow); exercise_rows.append(erow)
    await session.flush()
    return {"version_id": str(version.id), "version_number": number, "created": True, "weeks": len(week_rows), "sessions": len(session_rows), "exercises": len(exercise_rows), "checksum": checksum}

def _int(value: Any) -> int | None:
    try: return int(value) if value is not None and str(value).strip() else None
    except (TypeError, ValueError): return None

async def create_or_get_review(session, plan: LeoFTTrainingPlan, *, version_id: uuid.UUID, payload: dict[str, Any] | None = None, idempotency_key: str | None = None) -> LeoFTTrainingReviewSession:
    payload = dict(payload or {})
    key = idempotency_key or f"leoft-review:{plan.id}:{canonical_checksum(dict(plan.plan_json or {}))}"
    public_id = _safe_review_public_id(payload.get("review_public_id"))
    plan_json = dict(plan.plan_json or {})
    total_pages = _review_total_pages(plan_json, payload)
    current_page = _review_current_page(payload, total_pages)
    status = _review_status(payload)

    existing = None
    if public_id:
        existing = await session.scalar(select(LeoFTTrainingReviewSession).where(LeoFTTrainingReviewSession.review_public_id == public_id))
    if not existing:
        existing = await session.scalar(select(LeoFTTrainingReviewSession).where(LeoFTTrainingReviewSession.idempotency_key == key))
    if not existing:
        existing = await session.scalar(
            select(LeoFTTrainingReviewSession)
            .where(LeoFTTrainingReviewSession.plan_id == plan.id, LeoFTTrainingReviewSession.version_id == version_id)
            .order_by(LeoFTTrainingReviewSession.created_at.desc())
        )
    if existing:
        if existing.plan_id != plan.id or existing.version_id != version_id:
            raise ValueError("review_plan_version_conflict")
        if public_id and existing.review_public_id != public_id:
            conflict = await session.scalar(select(LeoFTTrainingReviewSession).where(LeoFTTrainingReviewSession.review_public_id == public_id))
            if conflict and conflict.id != existing.id:
                raise ValueError("review_public_id_conflict")
            existing.review_public_id = public_id
        if not existing.idempotency_key and key:
            existing.idempotency_key = key
        existing.total_pages = max(int(existing.total_pages or 1), total_pages)
        existing.current_page = max(int(existing.current_page or 0), current_page)
        if existing.status in {"pending", "reviewing", "awaiting_confirmation", "failed"}:
            existing.status = status
        existing.telegram_chat_id = _text(payload, "telegram_chat_id", "chat_id") or existing.telegram_chat_id
        existing.telegram_message_id = _text(payload, "telegram_message_id", "message_id") or existing.telegram_message_id
        existing.telegram_user_id = _text(payload, "telegram_user_id", "actor_user_id", "user_id") or existing.telegram_user_id
        existing.reviewer_identifier = _text(payload, "reviewer_identifier", "actor", "actor_user_id") or existing.reviewer_identifier
        await session.flush()
        return existing
    version = await session.get(LeoFTTrainingPlanVersion, version_id)
    if not version or version.plan_id != plan.id:
        raise ValueError("version_not_found")
    review = LeoFTTrainingReviewSession(
        review_public_id=public_id or f"leoft-{plan.id}-{str(version_id)[:8]}",
        plan_id=plan.id,
        version_id=version_id,
        status=status,
        current_page=current_page,
        total_pages=total_pages,
        telegram_chat_id=_text(payload, "telegram_chat_id", "chat_id"),
        telegram_message_id=_text(payload, "telegram_message_id", "message_id"),
        telegram_user_id=_text(payload, "telegram_user_id", "actor_user_id", "user_id"),
        reviewer_identifier=_text(payload, "reviewer_identifier", "actor", "actor_user_id"),
        idempotency_key=key,
    )
    session.add(review); await session.flush()
    session.add(LeoFTTrainingReviewEvent(review_session_id=review.id, plan_id=plan.id, event_type="review_created", page_after=current_page, actor_type="system", idempotency_key=key, payload_sanitized={"source": "agentos", "review_public_id": bool(public_id)}, result_status="created"))
    return review

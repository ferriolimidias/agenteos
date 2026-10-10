from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.leoft_security import hash_token
from db.models import (
    LeoFTStudentAccess,
    LeoFTStudentExerciseProgress,
    LeoFTStudentWorkoutProgress,
    LeoFTTrainingExercise,
    LeoFTTrainingPlan,
    LeoFTTrainingSession,
)


def _not_found(message: str = "Recurso LeoFT não encontrado.") -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message)


def _canonical_exercise_id(exercise_id: str, workout_id: str, workouts: dict[str, set[str]]) -> str:
    if exercise_id in workouts.get(workout_id, set()):
        return exercise_id
    prefix = f"{workout_id}:"
    candidate = next((item for item in workouts.get(workout_id, set()) if item.startswith(prefix) and item[len(prefix):] == exercise_id), None)
    return candidate or exercise_id


def _resolve_exercise(exercise_id: str, exercise_to_workout: dict[str, str], workouts: dict[str, set[str]]) -> tuple[str | None, str | None]:
    workout_id = exercise_to_workout.get(exercise_id)
    if workout_id:
        return workout_id, _canonical_exercise_id(exercise_id, workout_id, workouts)
    for candidate_workout, exercise_ids in workouts.items():
        if any(item.split(":", 1)[-1] == exercise_id for item in exercise_ids):
            candidate = next(item for item in exercise_ids if item.split(":", 1)[-1] == exercise_id)
            return candidate_workout, candidate
    return None, None


async def resolve_public_context(db: AsyncSession, token: str) -> tuple[LeoFTStudentAccess, LeoFTTrainingPlan]:
    result = await db.execute(
        select(LeoFTStudentAccess, LeoFTTrainingPlan)
        .join(LeoFTTrainingPlan, LeoFTTrainingPlan.id == LeoFTStudentAccess.plan_id)
        .where(
            LeoFTStudentAccess.access_token_hash == hash_token(token),
            LeoFTStudentAccess.status == "active",
            LeoFTStudentAccess.revoked_at.is_(None),
        )
    )
    row = result.first()
    if not row:
        raise _not_found("Acesso inválido ou expirado.")
    access, plan = row
    if plan.status != "approved":
        raise _not_found("Plano ainda não liberado.")
    return access, plan


async def resolve_public_access(db: AsyncSession, token: str) -> LeoFTStudentAccess:
    result = await db.execute(
        select(LeoFTStudentAccess).where(
            LeoFTStudentAccess.access_token_hash == hash_token(token),
            LeoFTStudentAccess.status == "active",
            LeoFTStudentAccess.revoked_at.is_(None),
        )
    )
    access = result.scalar_one_or_none()
    if not access:
        raise _not_found("Acesso inválido ou expirado.")
    return access


async def _plan_catalog(db: AsyncSession, plan_id: Any) -> tuple[dict[str, str], dict[str, set[str]], set[str]]:
    rows = (
        await db.execute(
            select(LeoFTTrainingExercise.external_exercise_id, LeoFTTrainingSession.external_session_id)
            .join(LeoFTTrainingSession, LeoFTTrainingSession.id == LeoFTTrainingExercise.session_id)
            .where(LeoFTTrainingExercise.plan_id == plan_id)
        )
    ).all()
    raw_counts: dict[str, int] = {}
    for exercise, _ in rows:
        raw_counts[str(exercise)] = raw_counts.get(str(exercise), 0) + 1
    exercise_to_workout: dict[str, str] = {}
    workouts: dict[str, set[str]] = {}
    for raw_exercise_id, raw_workout_id in rows:
        raw_exercise_id, raw_workout_id = str(raw_exercise_id), str(raw_workout_id)
        exercise_id = raw_exercise_id if raw_counts[raw_exercise_id] == 1 else f"{raw_workout_id}:{raw_exercise_id}"
        if raw_counts[raw_exercise_id] == 1:
            exercise_to_workout[raw_exercise_id] = raw_workout_id
        exercise_to_workout[exercise_id] = raw_workout_id
        workouts.setdefault(raw_workout_id, set()).add(exercise_id)
    if workouts:
        return exercise_to_workout, workouts, set(workouts)
    raise _not_found("Estrutura do plano indisponível.")


async def _catalog_or_plan_json(db: AsyncSession, plan: LeoFTTrainingPlan) -> tuple[dict[str, str], dict[str, set[str]], set[str]]:
    try:
        return await _plan_catalog(db, plan.id)
    except HTTPException:
        exercise_to_workout: dict[str, str] = {}
        workouts: dict[str, set[str]] = {}
        for month in (plan.plan_json or {}).get("months", []):
            for week in month.get("weeks", []):
                for workout in week.get("workouts", []):
                    workout_id = str(workout.get("workoutId") or "")
                    if not workout_id:
                        continue
                    for exercise in workout.get("exercises", []):
                        exercise_id = str(exercise.get("exerciseId") or "")
                        if exercise_id:
                            exercise_to_workout.setdefault(exercise_id, workout_id)
                            workouts.setdefault(workout_id, set()).add(exercise_id)
        if not workouts:
            raise _not_found("Estrutura do plano indisponível.")
        return exercise_to_workout, workouts, set(workouts)


async def _consolidated(db: AsyncSession, access: LeoFTStudentAccess, plan: LeoFTTrainingPlan) -> dict[str, Any]:
    exercise_to_workout, workouts, workout_ids = await _catalog_or_plan_json(db, plan)
    exercise_rows = (
        await db.execute(
            select(LeoFTStudentExerciseProgress)
            .where(
                LeoFTStudentExerciseProgress.access_id == access.id,
                LeoFTStudentExerciseProgress.plan_id == plan.id,
                LeoFTStudentExerciseProgress.status == "completed",
                LeoFTStudentExerciseProgress.exercise_id.in_(set(exercise_to_workout)),
            )
            .order_by(LeoFTStudentExerciseProgress.completed_at)
        )
    ).scalars().all()
    workout_rows = (
        await db.execute(
            select(LeoFTStudentWorkoutProgress)
            .where(
                LeoFTStudentWorkoutProgress.access_id == access.id,
                LeoFTStudentWorkoutProgress.plan_id == plan.id,
                LeoFTStudentWorkoutProgress.status == "completed",
                LeoFTStudentWorkoutProgress.workout_id.in_(workout_ids),
            )
            .order_by(LeoFTStudentWorkoutProgress.completed_at)
        )
    ).scalars().all()
    exercise_records = [
        {"exercise_id": row.exercise_id, "workout_id": row.workout_id, "status": row.status, "completed_at": row.completed_at.isoformat()}
        for row in exercise_rows
    ]
    workout_records = [
        {"workout_id": row.workout_id, "status": row.status, "completion_source": row.completion_source, "completed_at": row.completed_at.isoformat()}
        for row in workout_rows
    ]
    completed_exercises = len(exercise_records)
    completed_workouts = len(workout_records)
    total_exercises = len(exercise_to_workout)
    total_workouts = len(workout_ids)
    last_values = [row.completed_at for row in [*exercise_rows, *workout_rows] if row.completed_at]
    last_activity = max(last_values).isoformat() if last_values else None
    return {
        "plan_id": str(plan.id),
        "completed_exercise_ids": [row["exercise_id"] for row in exercise_records],
        "completed_workout_ids": [row["workout_id"] for row in workout_records],
        "completed_exercises": completed_exercises,
        "total_exercises": total_exercises,
        "completed_workouts": completed_workouts,
        "total_workouts": total_workouts,
        "percentage": round(completed_exercises * 100 / total_exercises) if total_exercises else 0,
        "workout_percentage": round(completed_workouts * 100 / total_workouts) if total_workouts else 0,
        "last_activity_at": last_activity,
        "exercise_records": exercise_records,
        "workout_records": workout_records,
    }


async def get_progress(db: AsyncSession, token: str) -> dict[str, Any]:
    access, plan = await resolve_public_context(db, token)
    return await _consolidated(db, access, plan)


async def _ensure_auto_workout(db: AsyncSession, access: LeoFTStudentAccess, plan: LeoFTTrainingPlan, workout_id: str, exercise_ids: set[str]) -> None:
    if not exercise_ids:
        return
    completed = (
        await db.execute(
            select(func.count(LeoFTStudentExerciseProgress.id)).where(
                LeoFTStudentExerciseProgress.access_id == access.id,
                LeoFTStudentExerciseProgress.plan_id == plan.id,
                LeoFTStudentExerciseProgress.workout_id == workout_id,
                LeoFTStudentExerciseProgress.exercise_id.in_(exercise_ids),
                LeoFTStudentExerciseProgress.status == "completed",
            )
        )
    ).scalar_one()
    if completed == len(exercise_ids):
        now = datetime.now(timezone.utc)
        statement = insert(LeoFTStudentWorkoutProgress).values(
            empresa_id=access.empresa_id, access_id=access.id, plan_id=plan.id,
            workout_id=workout_id, completion_source="automatic", status="completed",
            completed_at=now, created_at=now, updated_at=now,
        ).on_conflict_do_nothing(index_elements=["access_id", "plan_id", "workout_id"])
        await db.execute(statement)


async def complete_exercise(db: AsyncSession, token: str, exercise_id: str) -> dict[str, Any]:
    access, plan = await resolve_public_context(db, token)
    exercise_to_workout, workouts, _ = await _catalog_or_plan_json(db, plan)
    workout_id, exercise_id = _resolve_exercise(exercise_id, exercise_to_workout, workouts)
    if not workout_id:
        raise _not_found("Exercício não pertence ao plano.")
    now = datetime.now(timezone.utc)
    statement = insert(LeoFTStudentExerciseProgress).values(
        empresa_id=access.empresa_id, access_id=access.id, plan_id=plan.id,
        exercise_id=exercise_id, workout_id=workout_id, status="completed",
        completed_at=now, created_at=now, updated_at=now,
    ).on_conflict_do_update(
        index_elements=["access_id", "plan_id", "exercise_id"],
        set_={"workout_id": workout_id, "status": "completed", "updated_at": now},
    )
    await db.execute(statement)
    await _ensure_auto_workout(db, access, plan, workout_id, workouts[workout_id])
    return await _consolidated(db, access, plan)


async def delete_exercise(db: AsyncSession, token: str, exercise_id: str) -> dict[str, Any]:
    access, plan = await resolve_public_context(db, token)
    exercise_to_workout, workouts, _ = await _catalog_or_plan_json(db, plan)
    workout_id, canonical_exercise_id = _resolve_exercise(exercise_id, exercise_to_workout, workouts)
    if not workout_id:
        raise _not_found("Exercício não pertence ao plano.")
    await db.execute(delete(LeoFTStudentExerciseProgress).where(
        LeoFTStudentExerciseProgress.access_id == access.id,
        LeoFTStudentExerciseProgress.plan_id == plan.id,
        LeoFTStudentExerciseProgress.exercise_id == canonical_exercise_id,
    ))
    await db.execute(delete(LeoFTStudentWorkoutProgress).where(
        LeoFTStudentWorkoutProgress.access_id == access.id,
        LeoFTStudentWorkoutProgress.plan_id == plan.id,
        LeoFTStudentWorkoutProgress.workout_id == workout_id,
    ))
    return await _consolidated(db, access, plan)


async def complete_workout(db: AsyncSession, token: str, workout_id: str) -> dict[str, Any]:
    access, plan = await resolve_public_context(db, token)
    _, workouts, workout_ids = await _catalog_or_plan_json(db, plan)
    exercise_ids = workouts.get(workout_id)
    if workout_id not in workout_ids or exercise_ids is None:
        raise _not_found("Treino não pertence ao plano.")
    now = datetime.now(timezone.utc)
    for exercise_id in exercise_ids:
        statement = insert(LeoFTStudentExerciseProgress).values(
            empresa_id=access.empresa_id, access_id=access.id, plan_id=plan.id,
            exercise_id=exercise_id, workout_id=workout_id, status="completed",
            completed_at=now, created_at=now, updated_at=now,
        ).on_conflict_do_update(
            index_elements=["access_id", "plan_id", "exercise_id"],
            set_={"workout_id": workout_id, "status": "completed", "updated_at": now},
        )
        await db.execute(statement)
    statement = insert(LeoFTStudentWorkoutProgress).values(
        empresa_id=access.empresa_id, access_id=access.id, plan_id=plan.id,
        workout_id=workout_id, completion_source="manual", status="completed",
        completed_at=now, created_at=now, updated_at=now,
    ).on_conflict_do_update(
        index_elements=["access_id", "plan_id", "workout_id"],
        set_={"completion_source": "manual", "status": "completed", "updated_at": now},
    )
    await db.execute(statement)
    return await _consolidated(db, access, plan)


async def delete_workout(db: AsyncSession, token: str, workout_id: str) -> dict[str, Any]:
    access, plan = await resolve_public_context(db, token)
    _, workouts, workout_ids = await _catalog_or_plan_json(db, plan)
    if workout_id not in workout_ids:
        raise _not_found("Treino não pertence ao plano.")
    await db.execute(delete(LeoFTStudentWorkoutProgress).where(
        LeoFTStudentWorkoutProgress.access_id == access.id,
        LeoFTStudentWorkoutProgress.plan_id == plan.id,
        LeoFTStudentWorkoutProgress.workout_id == workout_id,
    ))
    await db.execute(delete(LeoFTStudentExerciseProgress).where(
        LeoFTStudentExerciseProgress.access_id == access.id,
        LeoFTStudentExerciseProgress.plan_id == plan.id,
        LeoFTStudentExerciseProgress.workout_id == workout_id,
    ))
    return await _consolidated(db, access, plan)

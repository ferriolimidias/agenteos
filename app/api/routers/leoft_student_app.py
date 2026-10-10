from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid import UUID

from app.services.leoft_plan_contract import normalize_leoft_plan_for_app
from app.services.leoft_student_app_access_service import public_access_payload
from app.services.leoft_training_approval_token_service import approve_training_plan_by_token
from app.services.leoft_progress_service import (
    complete_exercise,
    complete_workout,
    delete_exercise,
    delete_workout,
    get_progress,
    resolve_public_access,
    resolve_public_context,
)
from db.database import get_db
from db.models import (
    CRMLead,
    LeoFTCatalogEntry,
    LeoFTCatalogMedia,
    LeoFTManualWorkout,
    LeoFTManualWorkoutExercise,
    LeoFTStudent,
    LeoFTStudentAccess,
    LeoFTStudentWorkoutAssignment,
    LeoFTTrainingPlan,
)

router = APIRouter(prefix="/api/public/leoft-app", tags=["LeoFT Student App"])
approval_router = APIRouter(prefix="/api/public/leoft-training", tags=["LeoFT Training Approval"])


@router.get("/access/session")
async def leoft_app_access_session(
    token: str = Query(..., min_length=16, max_length=512),
    db: AsyncSession = Depends(get_db),
):
    access = await resolve_public_access(db, token)
    plan = await db.get(LeoFTTrainingPlan, access.plan_id) if access.plan_id else None
    lead = await db.get(CRMLead, access.lead_id) if access.lead_id else None
    student = await db.get(LeoFTStudent, access.student_id) if access.student_id else None
    if plan is not None and plan.status != "approved":
        plan = None
    student_name = (student.name if student else None) or (plan.student_name if plan else None) or getattr(lead, "nome_contato", None)
    return {
        "ok": True,
        "access": public_access_payload(access),
        "student": {
            "name": student_name,
            "email": access.student_email,
            "whatsapp": access.student_whatsapp,
        },
        "plan": ({
            "plan_id": str(plan.id),
            "status": plan.status,
            "product_name": plan.product_name,
        } if plan else None),
        "content": {"available": bool(plan), "workouts": []},
    }


async def _manual_public_student(db: AsyncSession, token: str) -> tuple[LeoFTStudentAccess, LeoFTStudent]:
    access = await resolve_public_access(db, token)
    if not access.student_id:
        raise HTTPException(status_code=404, detail="Aluno manual não encontrado.")
    student = await db.scalar(
        select(LeoFTStudent).where(
            LeoFTStudent.id == access.student_id,
            LeoFTStudent.empresa_id == access.empresa_id,
            LeoFTStudent.active.is_(True),
        )
    )
    if not student:
        raise HTTPException(status_code=404, detail="Aluno manual não encontrado.")
    return access, student


@router.get("/workouts")
async def leoft_app_manual_workouts(
    token: str = Query(..., min_length=16, max_length=512),
    db: AsyncSession = Depends(get_db),
):
    access, student = await _manual_public_student(db, token)
    rows = (
        await db.execute(
            select(LeoFTStudentWorkoutAssignment, LeoFTManualWorkout)
            .join(LeoFTManualWorkout, LeoFTManualWorkout.id == LeoFTStudentWorkoutAssignment.workout_id)
            .where(
                LeoFTStudentWorkoutAssignment.student_id == student.id,
                LeoFTStudentWorkoutAssignment.empresa_id == access.empresa_id,
                LeoFTManualWorkout.empresa_id == access.empresa_id,
                LeoFTManualWorkout.active.is_(True),
            )
            .order_by(LeoFTStudentWorkoutAssignment.position)
        )
    ).all()
    workouts = []
    for assignment, workout in rows:
        exercise_count = await db.scalar(
            select(func.count(LeoFTManualWorkoutExercise.id)).where(
                LeoFTManualWorkoutExercise.workout_id == workout.id
            )
        )
        workouts.append({
            "assignment_id": str(assignment.id),
            "workout_id": str(workout.id),
            "position": assignment.position,
            "status": assignment.status,
            "name": workout.name,
            "description": workout.description if assignment.status == "released" else None,
            "modality": workout.modality,
            "exercise_count": int(exercise_count or 0) if assignment.status == "released" else None,
        })
    return {"ok": True, "student": {"id": str(student.id), "name": student.name}, "workouts": workouts}


@router.get("/workouts/{assignment_id}")
async def leoft_app_manual_workout_detail(
    assignment_id: str = Path(..., min_length=1, max_length=80),
    token: str = Query(..., min_length=16, max_length=512),
    db: AsyncSession = Depends(get_db),
):
    try:
        parsed_assignment_id = UUID(assignment_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Treino não encontrado.") from None
    access, student = await _manual_public_student(db, token)
    row = (
        await db.execute(
            select(LeoFTStudentWorkoutAssignment, LeoFTManualWorkout)
            .join(LeoFTManualWorkout, LeoFTManualWorkout.id == LeoFTStudentWorkoutAssignment.workout_id)
            .where(
                LeoFTStudentWorkoutAssignment.id == parsed_assignment_id,
                LeoFTStudentWorkoutAssignment.student_id == student.id,
                LeoFTStudentWorkoutAssignment.empresa_id == access.empresa_id,
                LeoFTStudentWorkoutAssignment.status == "released",
                LeoFTManualWorkout.empresa_id == access.empresa_id,
                LeoFTManualWorkout.active.is_(True),
            )
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Treino não encontrado ou bloqueado.")
    assignment, workout = row
    exercise_rows = (
        await db.execute(
            select(LeoFTManualWorkoutExercise, LeoFTCatalogEntry)
            .join(LeoFTCatalogEntry, LeoFTCatalogEntry.id == LeoFTManualWorkoutExercise.exercise_id)
            .where(
                LeoFTManualWorkoutExercise.workout_id == workout.id,
                LeoFTCatalogEntry.active.is_(True),
            )
            .order_by(LeoFTManualWorkoutExercise.position)
        )
    ).all()
    exercises = []
    for item, exercise in exercise_rows:
        media = await db.scalar(
            select(LeoFTCatalogMedia.storage_path)
            .where(LeoFTCatalogMedia.catalog_entry_id == exercise.id)
            .order_by(LeoFTCatalogMedia.created_at.desc())
            .limit(1)
        )
        exercises.append({
            "id": str(item.id),
            "position": item.position,
            "name": exercise.canonical_name,
            "muscle_group": exercise.muscle_group,
            "equipment": exercise.equipment,
            "sets": item.sets,
            "reps": item.reps,
            "duration_seconds": item.duration_seconds,
            "rest_seconds": item.rest_seconds,
            "load": item.load,
            "notes": item.notes,
            "instructions": exercise.instructions,
            "media_url": exercise.media_url or media,
        })
    return {
        "ok": True,
        "student": {"id": str(student.id), "name": student.name},
        "assignment_id": str(assignment.id),
        "workout": {"id": str(workout.id), "name": workout.name, "description": workout.description, "modality": workout.modality, "exercises": exercises},
    }


@router.get("/plan")
async def leoft_app_plan(
    token: str = Query(..., min_length=16, max_length=512),
    db: AsyncSession = Depends(get_db),
):
    _, plan = await _active_access_and_approved_plan(db, token)
    return {"ok": True, "plan": normalize_leoft_plan_for_app(dict(plan.plan_json or {}))}


@router.get("/progress")
async def leoft_app_progress(
    token: str = Query(..., min_length=16, max_length=512),
    db: AsyncSession = Depends(get_db),
):
    return {"ok": True, "progress": await get_progress(db, token)}


@router.post("/progress/exercises/{exercise_id}/complete")
async def leoft_app_complete_exercise(
    exercise_id: str = Path(..., min_length=1, max_length=200),
    token: str = Query(..., min_length=16, max_length=512),
    db: AsyncSession = Depends(get_db),
):
    progress = await complete_exercise(db, token, exercise_id)
    await db.commit()
    return {"ok": True, "progress": progress}


@router.delete("/progress/exercises/{exercise_id}/complete")
async def leoft_app_delete_exercise(
    exercise_id: str = Path(..., min_length=1, max_length=200),
    token: str = Query(..., min_length=16, max_length=512),
    db: AsyncSession = Depends(get_db),
):
    progress = await delete_exercise(db, token, exercise_id)
    await db.commit()
    return {"ok": True, "progress": progress}


@router.post("/progress/workouts/{workout_id}/complete")
async def leoft_app_complete_workout(
    workout_id: str = Path(..., min_length=1, max_length=200),
    token: str = Query(..., min_length=16, max_length=512),
    db: AsyncSession = Depends(get_db),
):
    progress = await complete_workout(db, token, workout_id)
    await db.commit()
    return {"ok": True, "progress": progress}


@router.delete("/progress/workouts/{workout_id}/complete")
async def leoft_app_delete_workout(
    workout_id: str = Path(..., min_length=1, max_length=200),
    token: str = Query(..., min_length=16, max_length=512),
    db: AsyncSession = Depends(get_db),
):
    progress = await delete_workout(db, token, workout_id)
    await db.commit()
    return {"ok": True, "progress": progress}


async def _active_access_and_approved_plan(db: AsyncSession, token: str) -> tuple[LeoFTStudentAccess, LeoFTTrainingPlan]:
    return await resolve_public_context(db, token)


@approval_router.get("/approve")
async def approve_leoft_training_from_token(
    token: str = Query(..., min_length=32, max_length=512),
    db: AsyncSession = Depends(get_db),
):
    try:
        result = await approve_training_plan_by_token(db, token, approved_by="telegram/test")
    except ValueError:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Token de aprovação inválido ou expirado.")
    app_access = result.get("app_access") or {}
    app_link = app_access.get("app_link")
    app_link_html = f"<p><a href='{app_link}' rel='nofollow'>Abrir app do aluno</a></p>" if app_link else ""
    body = (
        "<!doctype html><html><head><meta charset='utf-8'>"
        "<meta name='robots' content='noindex,nofollow'>"
        "<title>Treino LeoFT aprovado</title></head>"
        "<body><h1>Treino aprovado com sucesso.</h1>"
        "<p>O aluno já pode acessar pelo app LeoFT.</p>"
        f"{app_link_html}"
        f"<p>Status: {result.get('status')}</p>"
        f"<p>Plano: {result.get('plan_id')}</p>"
        f"<p>Acesso: {app_access.get('access_token_masked') or 'liberado'}</p>"
        "</body></html>"
    )
    return HTMLResponse(body)

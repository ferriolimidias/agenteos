from __future__ import annotations

import copy
import json
from typing import Any

LEOFT_PLAN_SCHEMA_VERSION = "1.0"
ALLOWED_PLAN_STATUSES = {"pending_approval", "approved"}
ALLOWED_PRESCRIPTION_MODES = {"simple", "detailed", "cardio_interval", "time_based"}
ALLOWED_MEDIA_TYPES = {"gif", "video", "image", "none"}
SENSITIVE_KEYS = {
    "leadToken",
    "access_token",
    "raw_token",
    "token",
    "password",
    "temporary_password",
    "payment_reference",
    "paymentReference",
    "hermes_payload",
    "telegram_payload",
}


def validate_leoft_plan_contract(plan_json: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan_json, dict):
        raise ValueError("Plano LeoFT inválido: plan_json deve ser dict.")
    if plan_json.get("schemaVersion") != LEOFT_PLAN_SCHEMA_VERSION:
        raise ValueError("Plano LeoFT inválido: schemaVersion 1.0 obrigatório.")
    if str(plan_json.get("status") or "") not in ALLOWED_PLAN_STATUSES:
        raise ValueError("Plano LeoFT inválido: status não permitido.")
    if int(plan_json.get("durationMonths") or 0) != 3:
        raise ValueError("Plano LeoFT inválido: durationMonths deve ser 3.")
    for field in ("planId", "studentName", "productName"):
        if not _clean(plan_json.get(field)):
            raise ValueError(f"Plano LeoFT inválido: {field} obrigatório.")
    months = plan_json.get("months")
    if not isinstance(months, list) or len(months) != 3:
        raise ValueError("Plano LeoFT inválido: exatamente 3 meses obrigatórios.")

    workout_count = 0
    exercise_or_block_count = 0
    for month in months:
        if not isinstance(month, dict) or int(month.get("monthNumber") or 0) < 1:
            raise ValueError("Plano LeoFT inválido: monthNumber obrigatório.")
        weeks = month.get("weeks")
        if not isinstance(weeks, list) or not weeks:
            raise ValueError("Plano LeoFT inválido: cada mês precisa de semanas.")
        for week in weeks:
            if not isinstance(week, dict) or int(week.get("weekNumber") or 0) < 1:
                raise ValueError("Plano LeoFT inválido: weekNumber obrigatório.")
            workouts = week.get("workouts")
            if not isinstance(workouts, list) or not workouts:
                raise ValueError("Plano LeoFT inválido: cada semana precisa de treino.")
            for workout in workouts:
                _validate_workout(workout)
                workout_count += 1
                exercise_or_block_count += len(workout.get("exercises") or []) + len(workout.get("blocks") or [])

    if workout_count < 1:
        raise ValueError("Plano LeoFT inválido: ao menos 1 treino obrigatório.")
    if exercise_or_block_count < 1:
        raise ValueError("Plano LeoFT inválido: ao menos 1 exercício/bloco obrigatório.")
    _assert_no_sensitive_payload(plan_json)
    return copy.deepcopy(plan_json)


def normalize_leoft_plan_for_app(plan_json: dict[str, Any]) -> dict[str, Any]:
    plan = validate_leoft_plan_contract(plan_json)
    return {
        "schemaVersion": plan["schemaVersion"],
        "planId": plan["planId"],
        "studentName": plan["studentName"],
        "status": plan["status"],
        "durationMonths": plan["durationMonths"],
        "productName": plan["productName"],
        "months": plan["months"],
    }


def _validate_workout(workout: Any) -> None:
    if not isinstance(workout, dict):
        raise ValueError("Plano LeoFT inválido: workout deve ser dict.")
    for field in ("workoutId", "title", "type"):
        if not _clean(workout.get(field)):
            raise ValueError(f"Plano LeoFT inválido: workout.{field} obrigatório.")
    exercises = workout.get("exercises")
    blocks = workout.get("blocks")
    if not isinstance(exercises, list):
        raise ValueError("Plano LeoFT inválido: workout.exercises deve ser lista.")
    if blocks is not None and not isinstance(blocks, list):
        raise ValueError("Plano LeoFT inválido: workout.blocks deve ser lista.")
    if not exercises and not blocks:
        raise ValueError("Plano LeoFT inválido: workout precisa de exercícios ou blocos.")
    for exercise in exercises:
        _validate_exercise(exercise)


def _validate_exercise(exercise: Any) -> None:
    if not isinstance(exercise, dict):
        raise ValueError("Plano LeoFT inválido: exercise deve ser dict.")
    for field in ("exerciseId", "name", "prescriptionMode"):
        if not _clean(exercise.get(field)):
            raise ValueError(f"Plano LeoFT inválido: exercise.{field} obrigatório.")
    if exercise.get("prescriptionMode") not in ALLOWED_PRESCRIPTION_MODES:
        raise ValueError("Plano LeoFT inválido: prescriptionMode não permitido.")
    if not isinstance(exercise.get("seriesDetails", []), list):
        raise ValueError("Plano LeoFT inválido: seriesDetails deve ser lista.")
    if not isinstance(exercise.get("blocks", []), list):
        raise ValueError("Plano LeoFT inválido: blocks deve ser lista.")
    if not isinstance(exercise.get("instructions", []), list):
        raise ValueError("Plano LeoFT inválido: instructions deve ser lista.")
    media = exercise.get("media")
    if not isinstance(media, dict) or media.get("type") not in ALLOWED_MEDIA_TYPES or "url" not in media:
        raise ValueError("Plano LeoFT inválido: media inválida.")


def _assert_no_sensitive_payload(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if key in SENSITIVE_KEYS:
                raise ValueError(f"Plano LeoFT inválido: campo sensível proibido ({key}).")
            if key == "payment_status" and str(child).strip().lower() == "approved":
                raise ValueError("Plano LeoFT inválido: payment_status approved real proibido.")
            _assert_no_sensitive_payload(child)
    elif isinstance(value, list):
        for child in value:
            _assert_no_sensitive_payload(child)
    elif isinstance(value, str):
        serialized = value.lower()
        if "approved_simulated" not in serialized and '"payment_status": "approved"' in json.dumps(value):
            raise ValueError("Plano LeoFT inválido: pagamento real proibido.")


def _clean(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()

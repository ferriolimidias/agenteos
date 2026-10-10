from __future__ import annotations

import copy
import uuid
from collections.abc import Mapping
from typing import Any

from app.services.leoft_plan_contract import LEOFT_PLAN_SCHEMA_VERSION, validate_leoft_plan_contract


HERMES_REQUEST_SCHEMA_VERSION = "1.0"
HERMES_SOURCE = "agentos"
HERMES_EVENT = "leoft.payment_approved"
LEOFT_PRODUCT_NAME = "Método Híbrido LeoFT"
SENSITIVE_KEYS = {
    "leadToken",
    "lead_token",
    "access_token",
    "accessToken",
    "raw_token",
    "token",
    "password",
    "temporary_password",
    "temporaryPassword",
    "tokenReferenceHash",
    "payment_reference",
    "paymentReference",
    "reference_hash",
    "anamnese_token_hash",
    "telegram_payload",
    "telegramPayload",
    "hermes_payload",
    "api_key",
    "apikey",
    "secret",
    "authorization",
    "Authorization",
}


def build_hermes_leoft_payload_from_lead(
    *,
    lead: Any,
    empresa: Any | None = None,
    payment: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    dados = dict(getattr(lead, "dados_adicionais", None) or {})
    anamneses = dados.get("anamneses") if isinstance(dados.get("anamneses"), Mapping) else {}
    anamnese = dict((anamneses or {}).get("leoft") or {})
    respostas = dict(anamnese.get("respostas") or {})
    personal = dict(respostas.get("personalData") or {})
    training_profile = dict(respostas.get("trainingProfile") or {})
    status = dict(respostas.get("status") or {})
    payment_payload = dict(payment or {})
    payment_id = _text(payment_payload.get("payment_id") or payment_payload.get("id"))
    checkout_reference = _checkout_reference(
        lead=lead,
        payment=payment_payload,
    )
    empresa_id = _uuid_text(
        payment_payload.get("empresa_id")
        or payment_payload.get("empresaId")
        or getattr(lead, "empresa_id", "")
        or (getattr(empresa, "id", "") if empresa is not None else "")
    )
    lead_id = _uuid_text(payment_payload.get("lead_id") or payment_payload.get("leadId") or getattr(lead, "id", ""))
    anamnese_id = _anamnese_id(lead_id=lead_id, anamnese=anamnese, payment=payment_payload)

    payload = {
        "event": HERMES_EVENT,
        "schemaVersion": HERMES_REQUEST_SCHEMA_VERSION,
        "source": HERMES_SOURCE,
        "empresa_id": empresa_id,
        "lead_id": lead_id,
        "anamnese_id": anamnese_id,
        "checkout_reference": checkout_reference,
        "payment_id": payment_id,
        "amount": 97,
        "currency": "BRL",
        "payment_provider": "mercado_pago",
        "payment_status": _text(payment_payload.get("payment_status") or payment_payload.get("paymentStatus") or payment_payload.get("status")),
        "app_access": {
            "access_link_created": True,
        },
        "student": {
            "name": _text(personal.get("name") or getattr(lead, "nome_contato", "")),
            "email": _text(personal.get("email") or dados.get("email")),
            "whatsapp": _text(personal.get("whatsapp") or getattr(lead, "telefone_contato", "")),
        },
        "anamnese": {
            "personalData": personal,
            "trainingProfile": training_profile,
            "status": status,
        },
    }
    return validate_hermes_request_payload(payload)


def sanitize_hermes_payload(payload: dict[str, Any]) -> dict[str, Any]:
    return _sanitize(copy.deepcopy(payload))


def validate_hermes_request_payload(payload: dict[str, Any]) -> dict[str, Any]:
    clean = sanitize_hermes_payload(payload)
    if clean.get("schemaVersion") != HERMES_REQUEST_SCHEMA_VERSION:
        raise ValueError("Payload Hermes inválido: schemaVersion 1.0 obrigatório.")
    if clean.get("source") != HERMES_SOURCE:
        raise ValueError("Payload Hermes inválido: source agentos obrigatório.")
    if clean.get("event") != HERMES_EVENT:
        raise ValueError("Payload Hermes inválido: event leoft.payment_approved obrigatório.")
    if not _text(clean.get("empresa_id")):
        raise ValueError("missing_empresa_id")
    clean["empresa_id"] = _uuid_text(clean.get("empresa_id"), error_message="missing_empresa_id")
    if _text(clean.get("lead_id")):
        clean["lead_id"] = _uuid_text(clean.get("lead_id"), error_message="invalid_lead_id")
    if not _text(clean.get("anamnese_id")):
        raise ValueError("missing_anamnese_id")
    clean["anamnese_id"] = _text(clean.get("anamnese_id"))
    for field_name in ("checkout_reference", "payment_id", "amount", "currency", "payment_provider", "payment_status"):
        if not _text(clean.get(field_name)):
            raise ValueError(f"Payload Hermes inválido: {field_name} obrigatório.")
    if int(clean.get("amount") or 0) != 97:
        raise ValueError("Payload Hermes inválido: amount deve ser 97.")
    if str(clean.get("currency") or "").strip().upper() != "BRL":
        raise ValueError("Payload Hermes inválido: currency deve ser BRL.")
    if clean.get("payment_provider") != "mercado_pago":
        raise ValueError("Payload Hermes inválido: payment_provider deve ser mercado_pago.")
    if clean.get("payment_status") not in {"approved_simulated", "approved"}:
        raise ValueError("Payload Hermes inválido: payment_status não permitido.")
    if not isinstance(clean.get("app_access"), dict) or clean["app_access"].get("access_link_created") is not True:
        raise ValueError("Payload Hermes inválido: app_access.access_link_created obrigatório.")
    for section in ("student", "anamnese"):
        if not isinstance(clean.get(section), dict):
            raise ValueError(f"Payload Hermes inválido: {section} obrigatório.")
    student = clean["student"]
    if not _text(student.get("name")):
        raise ValueError("Payload Hermes inválido: student.name obrigatório.")
    return clean


def validate_hermes_response_payload(response: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise ValueError("Resposta Hermes inválida: JSON objeto obrigatório.")
    plan = normalize_hermes_response_to_plan_json(response)
    validate_leoft_plan_contract(plan)
    return copy.deepcopy(response)


def normalize_hermes_response_to_plan_json(response: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(response, dict):
        raise ValueError("Resposta Hermes inválida: JSON objeto obrigatório.")
    candidate = (
        response.get("plan_json")
        or response.get("planJson")
        or response.get("trainingPlan")
        or response.get("plan")
        or response.get("data")
        or response
    )
    if isinstance(candidate, Mapping) and isinstance(candidate.get("plan"), Mapping):
        candidate = candidate["plan"]
    if not isinstance(candidate, Mapping):
        raise ValueError("BLOQUEADO_CONTRATO_RETORNO: Hermes não retornou plan_json completo.")
    plan = dict(candidate)
    plan = _normalize_remote_plan_for_agentos(plan, response)
    if "schemaVersion" not in plan and any(key in response for key in ("plan_id", "planId", "status")):
        raise ValueError("BLOQUEADO_CONTRATO_RETORNO: Hermes aceitou o evento, mas não retornou plan_json completo.")
    if plan.get("schemaVersion") != LEOFT_PLAN_SCHEMA_VERSION:
        raise ValueError("BLOQUEADO_CONTRATO_RETORNO: schemaVersion do plan_json ausente ou inválido.")
    if plan.get("status") != "pending_approval":
        raise ValueError("Resposta Hermes inválida: plano deve retornar pending_approval.")
    return validate_leoft_plan_contract(plan)


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if str(key) in SENSITIVE_KEYS or "token" in str(key).lower() or "secret" in str(key).lower():
                continue
            result[key] = _sanitize(child)
        return result
    if isinstance(value, list):
        return [_sanitize(item) for item in value]
    return value


def _empresa_name(empresa: Any | None) -> str:
    if empresa is None:
        return "Leonardo Personal"
    for attr in ("nome_empresa", "nome", "name"):
        value = getattr(empresa, attr, None)
        if _text(value):
            return _text(value)
    if isinstance(empresa, Mapping):
        return _text(empresa.get("nome_empresa") or empresa.get("nome") or empresa.get("name")) or "Leonardo Personal"
    return "Leonardo Personal"


def _text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _uuid_text(value: Any, *, error_message: str = "invalid_empresa_id") -> str:
    text = _text(value)
    if not text:
        raise ValueError(error_message)
    try:
        return str(uuid.UUID(text))
    except (TypeError, ValueError) as exc:
        raise ValueError(error_message) from exc


def _anamnese_id(*, lead_id: str, anamnese: Mapping[str, Any], payment: Mapping[str, Any]) -> str:
    explicit = _text(
        payment.get("anamnese_id")
        or payment.get("anamneseId")
        or anamnese.get("anamnese_id")
        or anamnese.get("anamneseId")
        or anamnese.get("id")
    )
    if explicit:
        return explicit
    return f"leoft-anamnese:{lead_id}"


def _normalize_remote_plan_for_agentos(plan: dict[str, Any], response: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(plan)
    if not _text(normalized.get("status")) and _remote_response_was_accepted(response):
        normalized["status"] = "pending_approval"
    elif _text(normalized.get("status")) in {"ok", "generated", "processed", "created", "pending", "pending_review"} and _remote_response_was_accepted(response):
        normalized["status"] = "pending_approval"

    raw_plan_id = normalized.get("planId") or normalized.get("plan_id") or response.get("plan_id") or response.get("planId")
    if not _is_uuid_text(raw_plan_id) and _text(raw_plan_id):
        normalized["hermesPlanId"] = _text(raw_plan_id)
        normalized["planId"] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"agentos:hermes:leoft:plan:{_text(raw_plan_id)}"))
    elif _is_uuid_text(raw_plan_id):
        normalized["planId"] = str(uuid.UUID(_text(raw_plan_id)))
    normalized.setdefault("studentName", _student_name_from_remote_plan(normalized))
    normalized.setdefault("productName", LEOFT_PRODUCT_NAME)
    if "durationMonths" not in normalized and int(normalized.get("durationWeeks") or 0) == 12:
        normalized["durationMonths"] = 3
    if "months" not in normalized and isinstance(normalized.get("weeks"), list):
        normalized["months"] = _months_from_remote_weeks(normalized.get("weeks") or [], normalized.get("phases") or [])
    return normalized


def _student_name_from_remote_plan(plan: Mapping[str, Any]) -> str:
    aluno = plan.get("aluno")
    if isinstance(aluno, Mapping):
        return _text(aluno.get("name") or aluno.get("nome") or aluno.get("studentName")) or "Aluno LeoFT"
    return _text(plan.get("studentName") or aluno) or "Aluno LeoFT"


def _months_from_remote_weeks(weeks: list[Any], phases: Any) -> list[dict[str, Any]]:
    weeks_by_number = {}
    for fallback_index, week in enumerate(weeks, start=1):
        if not isinstance(week, Mapping):
            continue
        week_number = _positive_int(week.get("week_number") or week.get("weekNumber"), fallback_index)
        weeks_by_number[week_number] = week

    month_specs = _month_specs_from_phases(phases, sorted(weeks_by_number))
    months = []
    for month_number, title, week_numbers in month_specs:
        local_weeks = []
        for week_number in week_numbers:
            remote_week = weeks_by_number.get(week_number)
            if remote_week is None:
                continue
            local_weeks.append(_local_week_from_remote(remote_week, week_number))
        if local_weeks:
            months.append({"monthNumber": month_number, "title": title, "weeks": local_weeks})
    return months


def _month_specs_from_phases(phases: Any, week_numbers: list[int]) -> list[tuple[int, str, list[int]]]:
    if isinstance(phases, list) and phases:
        specs = []
        for index, phase in enumerate(phases[:3], start=1):
            if isinstance(phase, Mapping):
                title = _text(phase.get("title") or phase.get("label") or f"Mes {index}")
                numbers = [_positive_int(item, 0) for item in phase.get("weeks") or []]
                numbers = [item for item in numbers if item > 0]
                specs.append((index, title, numbers))
        if specs:
            return specs
    return [(index, f"Mes {index}", week_numbers[(index - 1) * 4 : index * 4]) for index in (1, 2, 3)]


def _local_week_from_remote(week: Mapping[str, Any], week_number: int) -> dict[str, Any]:
    sessions = week.get("sessions") if isinstance(week.get("sessions"), list) else []
    workouts = [_local_workout_from_remote(session, week_number, index) for index, session in enumerate(sessions, start=1) if isinstance(session, Mapping)]
    return {
        "weekNumber": week_number,
        "focus": _text(week.get("focus") or week.get("phase") or f"Semana {week_number}"),
        "workouts": workouts,
    }


def _local_workout_from_remote(session: Mapping[str, Any], week_number: int, index: int) -> dict[str, Any]:
    exercises = session.get("exercises") if isinstance(session.get("exercises"), list) else []
    return {
        "workoutId": _text(session.get("session_id") or session.get("sessionId") or f"w{week_number:02d}_s{index:02d}"),
        "title": _text(session.get("title") or session.get("day_label") or f"Treino {index}"),
        "type": _text(session.get("type") or "strength"),
        "exercises": [_local_exercise_from_remote(exercise, index) for index, exercise in enumerate(exercises, start=1) if isinstance(exercise, Mapping)],
        "blocks": [],
    }


def _local_exercise_from_remote(exercise: Mapping[str, Any], index: int) -> dict[str, Any]:
    gif_url = _text(exercise.get("gif_url") or exercise.get("gifUrl"))
    duration = _text(exercise.get("duration"))
    reps = _text(exercise.get("reps"))
    sets = _text(exercise.get("sets"))
    prescription_mode = "time_based" if duration and not reps and not sets else "simple"
    return {
        "exerciseId": _text(exercise.get("exercise_id") or exercise.get("exerciseId") or f"exercise-{index}"),
        "name": _text(exercise.get("name") or f"Exercicio {index}"),
        "category": _text(exercise.get("category") or exercise.get("group") or "Geral"),
        "muscleGroup": _text(exercise.get("muscleGroup") or exercise.get("group") or "Geral"),
        "equipment": _text(exercise.get("equipment") or ""),
        "prescriptionMode": prescription_mode,
        "sets": sets,
        "reps": reps,
        "duration": duration,
        "distance": _text(exercise.get("distance")),
        "rest": _text(exercise.get("rest")),
        "intensity": _text(exercise.get("intensity")),
        "tempo": _text(exercise.get("tempo")),
        "notes": _text(exercise.get("notes")),
        "seriesDetails": exercise.get("seriesDetails") if isinstance(exercise.get("seriesDetails"), list) else [],
        "blocks": exercise.get("blocks") if isinstance(exercise.get("blocks"), list) else [],
        "media": {"type": "gif" if gif_url else "none", "url": gif_url},
        "instructions": _instructions_from_remote(exercise.get("instructions")),
    }


def _instructions_from_remote(value: Any) -> list[str]:
    if isinstance(value, list):
        return [_text(item) for item in value if _text(item)]
    text = _text(value)
    return [text] if text else []


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


def _remote_response_was_accepted(response: Mapping[str, Any]) -> bool:
    return str(response.get("status") or "").strip().lower() == "ok" or response.get("processed") is True or response.get("generated") is True


def _is_uuid_text(value: Any) -> bool:
    text = _text(value)
    if not text:
        return False
    try:
        uuid.UUID(text)
    except (TypeError, ValueError):
        return False
    return True


def _checkout_reference(*, lead: Any, payment: Mapping[str, Any]) -> str:
    explicit = _text(
        payment.get("checkout_reference")
        or payment.get("checkoutReference")
        or payment.get("reference_hash")
        or payment.get("external_reference_hash")
    )
    if explicit:
        return f"leoft:{explicit}"
    empresa_id = _text(getattr(lead, "empresa_id", ""))
    lead_id = _text(getattr(lead, "id", ""))
    if empresa_id and lead_id:
        return f"leoft:{empresa_id}:{lead_id}"
    raise ValueError("Payload Hermes inválido: checkout_reference obrigatório.")

"""Cliente AgentOS -> Hermes para revisão Telegram LeoFT."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlparse
from urllib.request import Request, urlopen

from app.services.leoft_hermes_hmac import REDACTED, build_hermes_hmac_headers, canonical_json_body
from app.services.leoft_training_approval_token_service import mask_approval_url

DEFAULT_REVIEW_ENDPOINT = "/api/internal/hermes/events/leoft/training-ready-for-review"
DEFAULT_REMOTE_BASE_URL = "https://app.leoft.com.br"
DEFAULT_AGENTOS_APPROVAL_HOST = "agenteos.ferriolimidias.com.br"
DEFAULT_TIMEOUT_SECONDS = 60
REVIEW_EVENT = "leoft.training_ready_for_review"
REVIEW_SCHEMA_VERSION = "1.0"


class HermesReviewClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class HermesReviewConfig:
    base_url: str
    review_endpoint: str = DEFAULT_REVIEW_ENDPOINT
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    @property
    def review_url(self) -> str:
        return urljoin(self.base_url.rstrip("/") + "/", self.review_endpoint.lstrip("/"))


class HermesReviewClient:
    def __init__(self, *, config: HermesReviewConfig | None = None) -> None:
        self.config = config
        self.last_response: dict[str, Any] | None = None

    def send_training_ready_for_review(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_payload = validate_review_payload(payload)
        config = self.config or load_hermes_review_config()
        body = canonical_json_body(request_payload)
        headers = build_hermes_hmac_headers(request_payload)
        request = Request(config.review_url, data=body, method="POST", headers=headers)
        try:
            with urlopen(request, timeout=config.timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
                status_code = int(getattr(response, "status", 0) or 0)
        except HTTPError as exc:
            detail = _safe_error_detail(exc)
            raise HermesReviewClientError(f"ERRO_HERMES_REVIEW: HTTP {exc.code}: {detail}") from exc
        except TimeoutError as exc:
            raise HermesReviewClientError("Timeout ao chamar Hermes review.") from exc
        except URLError as exc:
            raise HermesReviewClientError(f"Falha de rede ao chamar Hermes review: {exc.reason}") from exc

        if status_code < 200 or status_code >= 300:
            raise HermesReviewClientError(f"Hermes review retornou HTTP {status_code}.")
        response_payload = _decode_json(raw)
        self.last_response = response_payload
        return classify_review_response(response_payload, request_payload)


def build_training_ready_for_review_payload(
    *,
    empresa_id: str,
    lead_id: str,
    anamnese_id: str,
    plan_id: str,
    student: dict[str, Any],
    training_summary: dict[str, Any],
    plan_json: dict[str, Any],
    approval_url: str,
    test_mode: bool,
) -> dict[str, Any]:
    idempotency_key = build_review_idempotency_key(plan_id=plan_id, plan_json=plan_json)
    return validate_review_payload(
        {
            "event": REVIEW_EVENT,
            "schemaVersion": REVIEW_SCHEMA_VERSION,
            "source": "agentos",
            "empresa_id": str(empresa_id),
            "lead_id": str(lead_id),
            "anamnese_id": str(anamnese_id),
            "plan_id": str(plan_id),
            "student": {"name": str((student or {}).get("name") or "").strip()},
            "training_summary": dict(training_summary or {}),
            "plan_json": dict(plan_json or {}),
            "approval_url": str(approval_url),
            "test_mode": bool(test_mode),
            "idempotency_key": idempotency_key,
        }
    )


def build_review_idempotency_key(*, plan_id: str, plan_json: dict[str, Any]) -> str:
    plan_hash = hashlib.sha256(canonical_json_body(dict(plan_json or {}))).hexdigest()[:16]
    return f"leoft-review:{str(plan_id).strip()}:{plan_hash}"


def idempotency_key_hash(idempotency_key: str) -> str:
    return hashlib.sha256(str(idempotency_key or "").encode("utf-8")).hexdigest()


def validate_review_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise HermesReviewClientError("Payload review deve ser dict.")
    required = ("empresa_id", "lead_id", "anamnese_id", "plan_id")
    for field in required:
        if not str(payload.get(field) or "").strip():
            raise HermesReviewClientError(f"missing_{field}")
    student = payload.get("student")
    if not isinstance(student, dict) or not str(student.get("name") or "").strip():
        raise HermesReviewClientError("missing_student_name")
    plan_json = payload.get("plan_json")
    if not isinstance(plan_json, dict):
        raise HermesReviewClientError("plan_json_must_be_dict")
    if plan_json.get("status") != "pending_approval":
        raise HermesReviewClientError("plan_json_status_must_be_pending_approval")
    counts = count_plan_structure(plan_json)
    if counts["months"] < 1 or counts["weeks"] < 1 or counts["workouts"] < 1 or counts["exercises"] < 1:
        raise HermesReviewClientError("plan_json_incomplete_structure")
    _validate_approval_url(str(payload.get("approval_url") or ""))
    if not isinstance(payload.get("test_mode"), bool):
        raise HermesReviewClientError("test_mode_must_be_boolean")
    payload = dict(payload)
    payload["event"] = REVIEW_EVENT
    payload["schemaVersion"] = REVIEW_SCHEMA_VERSION
    payload["source"] = "agentos"
    payload["idempotency_key"] = str(payload.get("idempotency_key") or build_review_idempotency_key(plan_id=str(payload["plan_id"]), plan_json=plan_json))
    return payload


def count_plan_structure(plan_json: dict[str, Any]) -> dict[str, int]:
    months = plan_json.get("months") if isinstance(plan_json, dict) else []
    month_count = len(months) if isinstance(months, list) else 0
    week_count = 0
    workout_count = 0
    exercise_count = 0
    for month in months if isinstance(months, list) else []:
        weeks = month.get("weeks") if isinstance(month, dict) else []
        if not isinstance(weeks, list):
            continue
        week_count += len(weeks)
        for week in weeks:
            workouts = week.get("workouts") if isinstance(week, dict) else []
            if not isinstance(workouts, list):
                continue
            workout_count += len(workouts)
            for workout in workouts:
                exercises = workout.get("exercises") if isinstance(workout, dict) else []
                if isinstance(exercises, list):
                    exercise_count += len(exercises)
    return {"months": month_count, "weeks": week_count, "workouts": workout_count, "exercises": exercise_count}


def training_summary_from_plan(plan_json: dict[str, Any], training_profile: dict[str, Any] | None = None) -> dict[str, Any]:
    profile = training_profile or {}
    return {
        "mainGoal": _first_text(profile, "mainGoal", "goal", "objetivo") or _first_text(plan_json, "mainGoal", "goal") or "",
        "trainingType": _first_text(profile, "trainingType", "type", "modalidade") or _first_text(plan_json, "trainingType") or "",
        "currentLevel": _first_text(profile, "currentLevel", "level", "nivel") or _first_text(plan_json, "currentLevel") or "",
        "daysPerWeek": _first_text(profile, "daysPerWeek", "trainingFrequency", "frequency") or _first_text(plan_json, "daysPerWeek") or "",
        "durationWeeks": int(plan_json.get("durationWeeks") or int(plan_json.get("durationMonths") or 0) * 4 or 0),
    }


def classify_review_response(response: dict[str, Any], request_payload: dict[str, Any]) -> dict[str, Any]:
    sent = bool(response.get("sent"))
    status = "sent" if sent else "failed"
    message_id = response.get("telegram_message_id") or response.get("message_id")
    chat_masked = response.get("review_chat_id_masked") or response.get("chat_id_masked")
    group_name = response.get("group_name") or response.get("telegram_group_name")
    result = {
        "mode": "via_hermes",
        "status": status,
        "sent": sent,
        "telegram_message_id": message_id,
        "review_chat_id_masked": chat_masked,
        "group_name": group_name,
        "idempotent": bool(response.get("idempotent") or response.get("idempotency_hit")),
        "called_real_telegram": bool(response.get("called_real_telegram", sent)),
        "approval_url_masked": mask_approval_url(str(request_payload.get("approval_url") or "")),
        "review_event_idempotency_key_hash": idempotency_key_hash(str(request_payload.get("idempotency_key") or "")),
        "sent_at": str(response.get("sent_at") or datetime.utcnow().replace(microsecond=0).isoformat() + "Z"),
        "plan_counts": count_plan_structure(dict(request_payload.get("plan_json") or {})),
    }
    if not sent:
        result["error"] = _sanitize_error_value(response.get("error") or response.get("message") or "review_not_sent")
    return result


def dry_run_review_result(*, payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "mode": "via_hermes",
        "status": "dry_run",
        "sent": False,
        "called_real_telegram": False,
        "approval_url_masked": mask_approval_url(str(payload.get("approval_url") or "")),
        "review_event_idempotency_key_hash": idempotency_key_hash(str(payload.get("idempotency_key") or "")),
        "plan_counts": count_plan_structure(dict(payload.get("plan_json") or {})),
        "dry_run_reason": "send_real_telegram_via_hermes_not_enabled",
    }


def load_hermes_review_config() -> HermesReviewConfig:
    base_url = str(os.getenv("HERMES_REMOTE_BASE_URL") or DEFAULT_REMOTE_BASE_URL).strip().rstrip("/")
    endpoint = str(os.getenv("HERMES_LEOFT_TRAINING_REVIEW_ENDPOINT") or DEFAULT_REVIEW_ENDPOINT).strip()
    timeout = _timeout_seconds(os.getenv("HERMES_REMOTE_TIMEOUT_SECONDS"))
    if not base_url:
        raise HermesReviewClientError("HERMES_REMOTE_BASE_URL ausente.")
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    return HermesReviewConfig(base_url=base_url, review_endpoint=endpoint, timeout_seconds=timeout)


def _validate_approval_url(approval_url: str) -> None:
    parsed = urlparse(approval_url)
    if parsed.scheme != "https":
        raise HermesReviewClientError("approval_url_must_be_https")
    if parsed.hostname != DEFAULT_AGENTOS_APPROVAL_HOST:
        raise HermesReviewClientError("approval_url_invalid_host")
    if parsed.path != "/api/public/leoft-training/approve":
        raise HermesReviewClientError("approval_url_invalid_path")
    token = parse_qs(parsed.query).get("token") or []
    if not token or not str(token[0]).strip():
        raise HermesReviewClientError("approval_url_missing_token")


def _first_text(payload: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = payload.get(key) if isinstance(payload, dict) else None
        text = " ".join(str(value or "").split()).strip()
        if text:
            return text
    return ""


def _timeout_seconds(value: str | None) -> int:
    try:
        parsed = int(str(value or "").strip() or DEFAULT_TIMEOUT_SECONDS)
    except ValueError:
        parsed = DEFAULT_TIMEOUT_SECONDS
    return max(1, min(parsed, 300))


def _decode_json(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise HermesReviewClientError("Hermes review retornou JSON inválido.") from exc
    if not isinstance(payload, dict):
        raise HermesReviewClientError("Hermes review retornou JSON não-objeto.")
    return payload


def _safe_error_detail(exc: HTTPError) -> str:
    try:
        raw = exc.read(500).decode("utf-8", errors="replace")
    except Exception:
        return "sem detalhe"
    return _sanitize_error_value(raw) or "sem detalhe"


def _sanitize_error_value(value: Any) -> str:
    text = str(value or "")
    for marker in ("token=", "signature=", "secret="):
        if marker in text.lower():
            return REDACTED
    return text.replace("\n", " ")[:300]

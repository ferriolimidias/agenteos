"""Cliente Hermes LeoFT. O padrão permanece mock local."""
from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import Request, urlopen

from app.services.leoft_hermes_contract import (
    normalize_hermes_response_to_plan_json,
    validate_hermes_request_payload,
    validate_hermes_response_payload,
)
from app.services.leoft_hermes_hmac import (
    REDACTED,
    build_hermes_hmac_headers,
    canonical_json_body,
    hmac_config_status,
    mask_hmac_config_for_logs,
)
from app.services.leoft_plan_contract import LEOFT_PLAN_SCHEMA_VERSION, validate_leoft_plan_contract

DEFAULT_PAYMENT_APPROVED_ENDPOINT = "/api/internal/hermes/events/leoft/payment-approved"
DEFAULT_REMOTE_BASE_URL = "https://app.leoft.com.br"
DEFAULT_TIMEOUT_SECONDS = 60
VALID_MODES = {"mock", "real"}


class HermesTrainingClientError(RuntimeError):
    pass


@dataclass(frozen=True)
class HermesRemoteConfig:
    base_url: str
    payment_approved_endpoint: str = DEFAULT_PAYMENT_APPROVED_ENDPOINT
    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS

    @property
    def payment_approved_url(self) -> str:
        return _join_url(self.base_url, self.payment_approved_endpoint)


class HermesTrainingClient:
    def __init__(self, *, use_real: bool = False, remote_config: HermesRemoteConfig | None = None) -> None:
        self.use_real = bool(use_real)
        self.remote_config = remote_config
        self.last_remote_response: dict[str, Any] | None = None

    def generate_training_plan(self, payload: dict[str, Any], mode: str = "mock") -> dict[str, Any]:
        selected_mode = str(mode or "mock").strip().lower()
        if selected_mode not in VALID_MODES:
            raise HermesTrainingClientError(f"Modo Hermes inválido: {selected_mode}")
        if selected_mode == "real":
            if not self.use_real:
                raise HermesTrainingClientError("Hermes real exige use_real=True.")
            return self._generate_training_plan_real(payload)
        return self._generate_training_plan_mock(payload)

    def probe_route_get(self) -> dict[str, Any]:
        config = self.remote_config or load_hermes_remote_config(require_hmac_secret=False)
        request = Request(
            config.payment_approved_url,
            method="GET",
            headers={
                "Accept": "application/json",
            },
        )
        try:
            with urlopen(request, timeout=config.timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
                status = int(getattr(response, "status", 0) or 0)
        except HTTPError as exc:
            raw = _safe_error_detail(exc)
            return {"status_code": int(exc.code), "body": raw}
        except (TimeoutError, URLError) as exc:
            raise HermesTrainingClientError(f"Probe GET Hermes falhou: {exc.__class__.__name__}.") from exc
        return {"status_code": status, "body": raw[:300]}

    def _generate_training_plan_real(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_payload = validate_hermes_request_payload(payload)
        config = self.remote_config or load_hermes_remote_config(require_hmac_secret=True)
        body = canonical_json_body(request_payload)
        headers = build_hermes_hmac_headers(request_payload)
        request = Request(
            config.payment_approved_url,
            data=body,
            method="POST",
            headers=headers,
        )
        try:
            with urlopen(request, timeout=config.timeout_seconds) as response:
                raw = response.read().decode("utf-8", errors="replace")
                status = int(getattr(response, "status", 0) or 0)
        except HTTPError as exc:
            detail = _safe_error_detail(exc)
            reason = _classify_http_error(exc.code, detail)
            raise HermesTrainingClientError(f"{reason}: HTTP {exc.code}: {detail}") from exc
        except TimeoutError as exc:
            raise HermesTrainingClientError("Timeout ao chamar Hermes remoto.") from exc
        except URLError as exc:
            raise HermesTrainingClientError(f"Falha de rede ao chamar Hermes remoto: {exc.reason}") from exc

        if status < 200 or status >= 300:
            raise HermesTrainingClientError(f"Hermes remoto retornou HTTP {status}.")
        response_payload = _decode_json(raw, context="payment-approved")
        self.last_remote_response = response_payload
        validate_hermes_response_payload(response_payload)
        return normalize_hermes_response_to_plan_json(response_payload)

    def _generate_training_plan_mock(self, payload: dict[str, Any]) -> dict[str, Any]:
        student = dict(payload.get("student") or {})
        plan = {
            "schemaVersion": LEOFT_PLAN_SCHEMA_VERSION,
            "planId": str(uuid.uuid4()),
            "studentName": str(student.get("name") or "Aluno Teste LeoFT"),
            "status": "pending_approval",
            "durationMonths": 3,
            "productName": "Método Híbrido LeoFT",
            "months": [
                {"monthNumber": 1, "title": "Base e adaptação", "weeks": [{"weekNumber": 1, "focus": "Retomada com segurança", "workouts": [
                    {"workoutId": "m1w1-strength-a", "title": "Treino A - Musculação", "type": "strength", "exercises": [
                        _exercise("ex-supino-reto", "Supino reto", "Peito", "Peitoral", "Barra", "simple", sets="3", reps="10 a 12", rest="60s", instructions=["Manter escápulas estabilizadas."]),
                        _exercise("ex-agachamento-progressivo", "Agachamento livre", "Pernas", "Quadríceps", "Barra", "detailed", rest="75s", seriesDetails=[
                            {"series": 1, "reps": "12", "intensity": "leve"},
                            {"series": 2, "reps": "10", "intensity": "moderada"},
                            {"series": 3, "reps": "8", "intensity": "pesada controlada"},
                        ], instructions=["Evitar impacto e priorizar amplitude sem dor."]),
                        _exercise("ex-prancha", "Prancha", "Core", "Abdômen", "Peso corporal", "time_based", duration="30s", rest="45s", instructions=["Interromper se perder alinhamento."]),
                    ], "blocks": []},
                    {"workoutId": "m1w1-run-b", "title": "Treino B - Corrida intervalada", "type": "cardio_interval", "exercises": [
                        _exercise("ex-corrida-intervalada", "Corrida intervalada leve", "Cardio", "Cardiorrespiratório", "Esteira ou rua", "cardio_interval", duration="25 min", intensity="leve a moderada", blocks=[
                            {"phase": "Aquecimento", "duration": "5 min caminhada leve"},
                            {"phase": "Principal", "rounds": 5, "work": "3 min corrida moderada", "recovery": "1 min caminhada leve"},
                            {"phase": "Desaquecimento", "duration": "5 min caminhada leve"},
                        ], instructions=["Manter intensidade conversável."]),
                    ], "blocks": [
                        {"phase": "Aquecimento", "duration": "5 min caminhada leve"},
                        {"phase": "Principal", "rounds": 5, "work": "3 min corrida moderada", "recovery": "1 min caminhada leve"},
                        {"phase": "Desaquecimento", "duration": "5 min caminhada leve"},
                    ]},
                ]}]},
                {"monthNumber": 2, "title": "Progressão", "weeks": [{"weekNumber": 5, "focus": "Aumentar consistência", "workouts": [
                    {"workoutId": "m2w5-strength-a", "title": "Treino A - Progressão", "type": "strength", "exercises": [
                        _exercise("ex-remada-baixa", "Remada baixa", "Costas", "Dorsais", "Máquina", "simple", sets="3", reps="10 a 12", rest="60s"),
                        _exercise("ex-bike-tempo", "Bike ergométrica", "Cardio", "Cardiorrespiratório", "Bike", "time_based", duration="20 min", intensity="moderada"),
                    ], "blocks": []},
                ]}]},
                {"monthNumber": 3, "title": "Consolidação", "weeks": [{"weekNumber": 9, "focus": "Consolidar rotina", "workouts": [
                    {"workoutId": "m3w9-strength-a", "title": "Treino A - Consolidação", "type": "strength", "exercises": [
                        _exercise("ex-leg-press", "Leg press", "Pernas", "Quadríceps e glúteos", "Máquina", "detailed", rest="75s", seriesDetails=[
                            {"series": 1, "reps": "12", "intensity": "moderada"},
                            {"series": 2, "reps": "10", "intensity": "moderada alta"},
                            {"series": 3, "reps": "10", "intensity": "moderada alta"},
                        ]),
                    ], "blocks": []},
                ]}]},
            ],
            "notes": [],
            "safetyWarnings": ["Respeitar desconforto no joelho e ajustar impacto."],
        }
        return validate_leoft_plan_contract(plan)


def _exercise(
    exercise_id: str,
    name: str,
    category: str,
    muscle_group: str,
    equipment: str,
    prescription_mode: str,
    *,
    sets: str = "",
    reps: str = "",
    duration: str = "",
    distance: str = "",
    rest: str = "",
    intensity: str = "",
    tempo: str = "",
    notes: str = "",
    seriesDetails: list[dict[str, Any]] | None = None,
    blocks: list[dict[str, Any]] | None = None,
    instructions: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "exerciseId": exercise_id,
        "name": name,
        "category": category,
        "muscleGroup": muscle_group,
        "equipment": equipment,
        "prescriptionMode": prescription_mode,
        "sets": sets,
        "reps": reps,
        "duration": duration,
        "distance": distance,
        "rest": rest,
        "intensity": intensity,
        "tempo": tempo,
        "notes": notes,
        "seriesDetails": seriesDetails or [],
        "blocks": blocks or [],
        "media": {"type": "none", "url": ""},
        "instructions": instructions or [],
    }


def load_hermes_remote_config(*, require_hmac_secret: bool = True, require_token: bool | None = None) -> HermesRemoteConfig:
    base_url = str(os.getenv("HERMES_REMOTE_BASE_URL") or DEFAULT_REMOTE_BASE_URL).strip().rstrip("/")
    endpoint = str(os.getenv("HERMES_LEOFT_PAYMENT_APPROVED_ENDPOINT") or DEFAULT_PAYMENT_APPROVED_ENDPOINT).strip()
    timeout_seconds = _timeout_seconds(os.getenv("HERMES_REMOTE_TIMEOUT_SECONDS"))
    missing = []
    if not base_url:
        missing.append("HERMES_REMOTE_BASE_URL")
    hmac_status = hmac_config_status()
    if require_hmac_secret:
        missing.extend(hmac_status["missing"])
    if missing:
        raise HermesTrainingClientError("Configuração Hermes remota ausente: " + ", ".join(missing))
    if not endpoint.startswith("/"):
        endpoint = "/" + endpoint
    return HermesRemoteConfig(
        base_url=base_url,
        payment_approved_endpoint=endpoint,
        timeout_seconds=timeout_seconds,
    )


def hermes_remote_config_status() -> dict[str, Any]:
    base_url = str(os.getenv("HERMES_REMOTE_BASE_URL") or DEFAULT_REMOTE_BASE_URL).strip()
    endpoint = str(os.getenv("HERMES_LEOFT_PAYMENT_APPROVED_ENDPOINT") or DEFAULT_PAYMENT_APPROVED_ENDPOINT).strip()
    timeout_seconds = _timeout_seconds(os.getenv("HERMES_REMOTE_TIMEOUT_SECONDS"))
    hmac_status = hmac_config_status()
    return {
        "base_url_present": bool(base_url),
        "base_url_defaulted": not bool(str(os.getenv("HERMES_REMOTE_BASE_URL") or "").strip()),
        "hmac_secret_present": bool(hmac_status["secret_present"]),
        "payment_approved_endpoint": endpoint or DEFAULT_PAYMENT_APPROVED_ENDPOINT,
        "timeout_seconds": timeout_seconds,
        "missing": [
            name
            for name, present in (
                ("HERMES_LEOFT_HMAC_SECRET", bool(hmac_status["secret_present"])),
                ("HERMES_LEOFT_HMAC_SIGNATURE_HEADER", bool(hmac_status["signature_header"])),
                ("HERMES_LEOFT_HMAC_TIMESTAMP_HEADER", bool(hmac_status["timestamp_header"])),
                ("HERMES_LEOFT_HMAC_EVENT_HEADER", bool(hmac_status["event_header"])),
                ("HERMES_LEOFT_HMAC_IDEMPOTENCY_HEADER", bool(hmac_status["idempotency_header"])),
            )
            if not present
        ],
        "base_url_masked": _mask_url(base_url),
        "hmac": mask_hmac_config_for_logs(hmac_status),
    }


def _timeout_seconds(value: str | None) -> int:
    try:
        parsed = int(str(value or "").strip() or DEFAULT_TIMEOUT_SECONDS)
    except ValueError:
        parsed = DEFAULT_TIMEOUT_SECONDS
    return max(1, min(parsed, 300))


def _join_url(base_url: str, endpoint: str) -> str:
    return urljoin(base_url.rstrip("/") + "/", endpoint.lstrip("/"))


def _decode_json(raw: str, *, context: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise HermesTrainingClientError(f"Hermes retornou JSON inválido em {context}.") from exc
    if not isinstance(payload, dict):
        raise HermesTrainingClientError(f"Hermes retornou JSON não-objeto em {context}.")
    return payload


def _safe_error_detail(exc: HTTPError) -> str:
    try:
        raw = exc.read(500).decode("utf-8", errors="replace")
    except Exception:
        return "sem detalhe"
    return _sanitize_error_detail(raw) or "sem detalhe"


def _mask_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    if "://" not in raw:
        return "***"
    scheme, rest = raw.split("://", 1)
    host = rest.split("/", 1)[0]
    return f"{scheme}://{host[:3]}***"


def _classify_http_error(status_code: int, detail: str) -> str:
    lowered = str(detail or "").lower()
    if int(status_code) == 403:
        return "Hermes rejeitou: allowlist/IP bloqueado"
    if int(status_code) == 400 and "missing_idempotency_key" in lowered:
        return "Hermes rejeitou: missing_idempotency_key"
    if int(status_code) == 400 and "wrong_event" in lowered:
        return "Hermes rejeitou: wrong_event"
    if int(status_code) == 400 and "invalid_signature" in lowered:
        return "Hermes rejeitou: invalid_signature"
    return "ERRO_HERMES_REMOTO"


def _sanitize_error_detail(raw: str) -> str:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return _truncate_text(raw)
    if isinstance(payload, dict):
        return _truncate_text(json.dumps(_sanitize_remote_value(payload), ensure_ascii=False, sort_keys=True))
    return _truncate_text(str(payload))


def _sanitize_remote_value(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, child in value.items():
            key_text = str(key).lower()
            if any(marker in key_text for marker in ("secret", "token", "password", "authorization")):
                sanitized[key] = REDACTED
            elif "signature" in key_text:
                sanitized[key] = _mask_signature(child)
            else:
                sanitized[key] = _sanitize_remote_value(child)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_remote_value(item) for item in value]
    return value


def _truncate_text(value: str) -> str:
    return str(value or "").replace("\n", " ")[:300]


def _mask_signature(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 12:
        return REDACTED
    return text[:6] + REDACTED + text[-6:]

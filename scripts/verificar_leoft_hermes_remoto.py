from __future__ import annotations

import argparse
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.leoft_hermes_contract import validate_hermes_request_payload  # noqa: E402
from app.services.leoft_hermes_hmac import build_hermes_hmac_headers, canonical_json_body  # noqa: E402
from app.services.leoft_hermes_training_client import (  # noqa: E402
    HermesTrainingClientError,
    hermes_remote_config_status,
    load_hermes_remote_config,
)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--show-config", action="store_true")
    parser.add_argument("--probe-route", action="store_true")
    parser.add_argument("--signed-fake-event", action="store_true")
    args = parser.parse_args()

    result = _base_result()
    exit_code = 0

    if args.show_config or not (args.probe_route or args.signed_fake_event):
        result["resultado"] = "BLOQUEADO_CONFIGURACAO" if result["missing_envs"] else "CONFIGURACAO_OK"
        print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
        return 0

    if args.probe_route:
        try:
            route = _probe_route()
            result.update(route)
            if route["allowlist_ok"]:
                result["resultado"] = "ROTA_ALCANCAVEL"
            else:
                result["resultado"] = "BLOQUEADO"
                exit_code = 1
        except HermesTrainingClientError as exc:
            result["resultado"] = "FALHOU"
            result["errors"].append(str(exc))
            exit_code = 1

    if args.signed_fake_event:
        if result["missing_envs"]:
            result["resultado"] = "BLOQUEADO_CONFIGURACAO"
            result["signed_fake_event"] = "NÃO_EXECUTADO"
            print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
            return 0
        try:
            signed = _signed_fake_event()
            result.update(signed)
            result["resultado"] = signed["resultado"]
            exit_code = 0 if signed["resultado"] == "PASSOU" else 1
        except (HermesTrainingClientError, ValueError) as exc:
            result["resultado"] = "FALHOU"
            result["signed_fake_event"] = "FALHOU"
            result["errors"].append(str(exc))
            exit_code = 1

    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return exit_code


def _base_result() -> dict[str, Any]:
    status = hermes_remote_config_status()
    return {
        "resultado": "NAO_EXECUTADO",
        "HERMES_REMOTE_BASE_URL_presente": status["base_url_present"],
        "HERMES_REMOTE_BASE_URL_defaulted": status["base_url_defaulted"],
        "HERMES_LEOFT_HMAC_SECRET_presente": status["hmac_secret_present"],
        "HERMES_LEOFT_HMAC_SECRET_masked": status["hmac"]["secret_masked"],
        "endpoint": status["payment_approved_endpoint"],
        "base_url_masked": status["base_url_masked"],
        "hmac_headers": {
            "signature": status["hmac"]["signature_header"],
            "timestamp": status["hmac"]["timestamp_header"],
            "event": status["hmac"]["event_header"],
            "idempotency": status["hmac"]["idempotency_header"],
        },
        "timeout_seconds": status["timeout_seconds"],
        "missing_envs": status["missing"],
        "get_probe": "NÃO_EXECUTADO",
        "post_empty_probe": "NÃO_EXECUTADO",
        "get_405": False,
        "post_empty_400_wrong_event": False,
        "allowlist_ok": False,
        "signed_fake_event": "NÃO_EXECUTADO",
        "signed_fake_event_ok": False,
        "called_external_services": None,
        "hermes_returns_plan_json": "NÃO_TESTADO",
        "errors": [],
    }


def _probe_route() -> dict[str, Any]:
    config = load_hermes_remote_config(require_hmac_secret=False)
    get_status, get_body = _request(config.payment_approved_url, method="GET", timeout=config.timeout_seconds)
    post_status, post_body = _request(
        config.payment_approved_url,
        method="POST",
        body=b"{}",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        timeout=config.timeout_seconds,
    )
    post_wrong_event = post_status == 400 and "wrong_event" in post_body.lower()
    return {
        "get_probe": get_status,
        "post_empty_probe": post_status,
        "get_405": get_status == 405,
        "post_empty_400_wrong_event": post_wrong_event,
        "allowlist_ok": get_status != 403 and post_status != 403 and (get_status == 405 or post_wrong_event),
        "probe_bodies": {
            "get": _truncate(get_body),
            "post_empty": _truncate(post_body),
        },
    }


def _signed_fake_event() -> dict[str, Any]:
    config = load_hermes_remote_config(require_hmac_secret=True)
    payload = _synthetic_payload()
    validate_hermes_request_payload(payload)
    body = canonical_json_body(payload)
    headers = build_hermes_hmac_headers(payload)
    status, raw = _request(
        config.payment_approved_url,
        method="POST",
        body=body,
        headers=headers,
        timeout=config.timeout_seconds,
    )
    response = _decode_json(raw)
    plan_json = response.get("plan_json") or response.get("planJson") or response.get("trainingPlan") or response.get("plan")
    plan_status = _first_present(response, "status_do_plano", "plan_status", "planStatus")
    if plan_status is None and isinstance(plan_json, dict):
        plan_status = _first_present(plan_json, "status", "status_do_plano", "planStatus")
    response_error = _first_present(response, "error", "erro", "reason", "message")
    return {
        "resultado": "PASSOU" if 200 <= status < 300 else "FALHOU",
        "signed_fake_event": "OK" if 200 <= status < 300 else "FALHOU",
        "signed_fake_event_ok": 200 <= status < 300,
        "signed_fake_event_status": status,
        "called_external_services": response.get("called_external_services"),
        "hermes_returns_plan_json": isinstance(plan_json, dict),
        "hermes_response_error": response_error,
        "hermes_response_status": response.get("status"),
        "hermes_response_dry_run": _first_present(response, "dry_run", "dryRun"),
        "hermes_response_idempotent": _first_present(response, "idempotent", "idempotency_hit", "idempotencyHit"),
        "hermes_response_plan_id": _first_present(response, "plan_id", "planId"),
        "hermes_response_plan_json_present": isinstance(plan_json, dict),
        "hermes_response_status_do_plano": plan_status,
        "hermes_response_training_status": plan_status or response.get("status"),
        "hermes_response_keys": sorted(response.keys()),
    }


def _synthetic_payload() -> dict[str, Any]:
    suffix = uuid.uuid4().hex
    lead_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"agentos:leoft:hermes:fake:{suffix}"))
    anamnese_id = f"leoft-anamnese-fake:{uuid.uuid5(uuid.NAMESPACE_URL, f'agentos:leoft:hermes:fake:anamnese:{suffix}')}"
    return {
        "event": "leoft.payment_approved",
        "schemaVersion": "1.0",
        "source": "agentos",
        "empresa_id": "a7384cb4-98b2-4750-94bc-3b329d35156d",
        "lead_id": lead_id,
        "anamnese_id": anamnese_id,
        "checkout_reference": f"leoft:fake:{suffix}",
        "payment_id": f"fake-hermes-payment-{suffix}",
        "amount": 97,
        "currency": "BRL",
        "payment_provider": "mercado_pago",
        "payment_status": "approved_simulated",
        "app_access": {"access_link_created": True},
        "student": {
            "name": "Aluno Fake Hermes HMAC",
            "email": "aluno.fake.hermes@example.com",
            "whatsapp": "5554999900000",
        },
        "anamnese": {
            "personalData": {"name": "Aluno Fake Hermes HMAC", "age": "35", "email": "aluno.fake.hermes@example.com", "whatsapp": "5554999900000"},
            "trainingProfile": {"mainGoal": "Teste assinado sem dados reais", "trainingType": "Musculação + corrida", "currentLevel": "intermediario", "daysPerWeek": "4", "timePerSession": "45"},
            "status": {"anamneseStarted": True, "anamneseCompleted": True, "checkoutStarted": True, "paymentStatus": "approved_simulated"},
        },
    }


def _request(url: str, *, method: str, timeout: int, body: bytes | None = None, headers: dict[str, str] | None = None) -> tuple[int, str]:
    request = Request(url, data=body, method=method, headers=headers or {})
    try:
        with urlopen(request, timeout=timeout) as response:
            return int(getattr(response, "status", 0) or 0), response.read().decode("utf-8", errors="replace")
    except HTTPError as exc:
        return int(exc.code), exc.read(1000).decode("utf-8", errors="replace")
    except TimeoutError as exc:
        raise HermesTrainingClientError("Timeout ao chamar Hermes remoto.") from exc
    except URLError as exc:
        raise HermesTrainingClientError(f"Falha de rede ao chamar Hermes remoto: {exc.reason}") from exc


def _decode_json(raw: str) -> dict[str, Any]:
    try:
        payload = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {"raw": _truncate(_sanitize_raw_text(raw))}
    return _sanitize_response(payload if isinstance(payload, dict) else {"raw": payload})


def _truncate(value: str) -> str:
    return str(value or "").replace("\n", " ")[:300]


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload:
            return payload.get(key)
    return None


def _sanitize_response(value: Any) -> Any:
    if isinstance(value, dict):
        sanitized = {}
        for key, child in value.items():
            key_text = str(key).lower()
            if any(marker in key_text for marker in ("secret", "token", "password", "authorization")):
                sanitized[key] = "***REDACTED***"
            elif "signature" in key_text:
                sanitized[key] = _mask_signature(child)
            else:
                sanitized[key] = _sanitize_response(child)
        return sanitized
    if isinstance(value, list):
        return [_sanitize_response(item) for item in value]
    return value


def _mask_signature(value: Any) -> str:
    text = str(value or "")
    if not text:
        return ""
    if len(text) <= 12:
        return "***REDACTED***"
    return text[:6] + "***REDACTED***" + text[-6:]


def _sanitize_raw_text(value: str) -> str:
    text = str(value or "")
    patterns = [
        (r"(?i)(authorization\s*[:=]\s*bearer\s+)[^\s\"'<>]+", r"\1***REDACTED***"),
        (r"(?i)((?:secret|token|password|access_token)\s*[:=]\s*)[^\s\"'<>]+", r"\1***REDACTED***"),
        (r"(?i)((?:signature)\s*[:=]\s*)([a-f0-9]{16,}|[^\s\"'<>]+)", r"\1***REDACTED***"),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


if __name__ == "__main__":
    raise SystemExit(main())

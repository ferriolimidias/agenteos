from __future__ import annotations

import json
import re
import sys
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.hermes.leoft_bridge import (  # noqa: E402
    canonical_event_payload,
    sign_event_payload,
)


FIXTURE_PATH = ROOT / "fixtures/leoft/agenteos_to_hermes_payment_approved.fixture.json"
HMAC_SECRET_FAKE = "fake-local-hermes-fixture-secret-ag3"
EXPECTED_SIGNATURE = "fcda1bb1caf510e5040484896651ff8720a28cf343501bd05bcbb32bebd14646"
EXPECTED_PAYLOAD = {
    "event": "leoft.payment_approved",
    "empresa_id": "fake_empresa_leoft",
    "lead_id": "fake_lead_ag3_contract",
    "anamnese_id": "fake_anamnese_ag3_contract",
    "checkout_reference": "leoft_chk_ag3_contract",
    "payment_provider": "mercado_pago",
    "payment_id": "mp_fake_ag3_contract",
    "amount": 97,
    "currency": "BRL",
    "app_access": {
        "app_url": "https://app.leoft.com.br",
        "access_link_created": True,
    },
}
REQUIRED_PAYLOAD_FIELDS = {
    "event",
    "empresa_id",
    "lead_id",
    "anamnese_id",
    "checkout_reference",
    "payment_provider",
    "payment_id",
    "amount",
    "currency",
    "app_access",
}


def record(results: dict[str, str], key: str, condition: bool, detail: Any = None, value: str = "ok") -> None:
    if not condition:
        raise AssertionError(f"{key}=fail {detail if detail is not None else ''}".strip())
    results[key] = value


def load_fixture() -> dict[str, Any]:
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def no_sensitive_data(value: Mapping[str, Any]) -> bool:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    forbidden_patterns = [
        r"sk-[A-Za-z0-9_-]{12,}",
        r"EAA[A-Za-z0-9]{20,}",
        r"APP_USR-[A-Za-z0-9_-]{12,}",
        r"TEST-[A-Za-z0-9_-]{12,}",
        r"xox[baprs]-[A-Za-z0-9-]{10,}",
        r"eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}",
        r"\+?55\d{10,11}",
        r"token=[A-Za-z0-9_-]+",
        r"access_token",
    ]
    return not any(re.search(pattern, text) for pattern in forbidden_patterns)


def source_has_no_real_calls() -> bool:
    source_paths = [
        ROOT / "scripts/verificar_leoft_agenteos_hermes_fixture_safe.py",
        ROOT / "app/services/hermes/leoft_bridge.py",
    ]
    forbidden = [
        "import " + "requests",
        "import " + "httpx",
        "Async" + "Client",
        "client." + "post(",
        "client." + "get(",
        "urllib." + "request",
        "EVOLUTION" + "_API",
        "OPENAI" + "_API",
    ]
    for path in source_paths:
        source = path.read_text(encoding="utf-8")
        if any(item in source for item in forbidden):
            return False
    return True


def main() -> int:
    results: dict[str, str] = {}

    record(results, "fixture_created", FIXTURE_PATH.exists())
    fixture = load_fixture()
    payload = fixture.get("payload")
    signature = fixture.get("signature") or {}
    headers = fixture.get("headers") or {}

    record(results, "event_schema", isinstance(payload, dict) and payload == EXPECTED_PAYLOAD)
    record(results, "required_fields", isinstance(payload, dict) and REQUIRED_PAYLOAD_FIELDS.issubset(payload.keys()))
    record(results, "checkout_reference", bool(str(payload.get("checkout_reference") or "").strip()))
    record(
        results,
        "app_access",
        isinstance(payload.get("app_access"), dict)
        and payload["app_access"].get("app_url") == "https://app.leoft.com.br"
        and payload["app_access"].get("access_link_created") is True,
    )
    record(
        results,
        "idempotency_key",
        fixture.get("idempotency_key") == "leoft.payment_approved:mp_fake_ag3_contract"
        and headers.get("X-AgenteOS-Idempotency-Key") == fixture.get("idempotency_key"),
    )
    expected_signature = sign_event_payload(payload, HMAC_SECRET_FAKE)
    record(
        results,
        "signature_mock",
        signature.get("algorithm") == "HMAC-SHA256"
        and signature.get("header") == "X-AgenteOS-Signature"
        and signature.get("value") == EXPECTED_SIGNATURE
        and headers.get("X-AgenteOS-Signature") == EXPECTED_SIGNATURE
        and expected_signature == EXPECTED_SIGNATURE
        and signature.get("signed_payload_sha256")
        == __import__("hashlib").sha256(canonical_event_payload(payload).encode("utf-8")).hexdigest(),
    )
    record(results, "no_secrets", no_sensitive_data(fixture))
    record(results, "external_calls", source_has_no_real_calls() and fixture.get("real_call") is False, value="blocked")

    print("AGENTEOS_HERMES_FIXTURE_OK")
    for key in [
        "fixture_created",
        "event_schema",
        "required_fields",
        "checkout_reference",
        "app_access",
        "idempotency_key",
        "signature_mock",
        "no_secrets",
        "external_calls",
    ]:
        print(f"{key}={results[key]}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

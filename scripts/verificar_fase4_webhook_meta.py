#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]


def require(condition: bool, message: str, failures: list[str]) -> None:
    print(f"CHECK {message}={'OK' if condition else 'FAIL'}")
    if not condition:
        failures.append(message)


def main() -> int:
    failures: list[str] = []
    files = (
        "app/services/meta_webhook.py",
        "app/api/routers/meta_webhook.py",
        "tests/channels/test_meta_webhook.py",
    )
    for rel in files:
        require((ROOT / rel).exists(), f"exists {rel}", failures)

    service = (ROOT / "app/services/meta_webhook.py").read_text(encoding="utf-8")
    for fragment in (
        "verify_meta_signature",
        "hmac.compare_digest",
        "normalize_meta_payload",
        "resolve_meta_connection",
        "Conexao.phone_number_id == event.external_account_id",
        "Conexao.instagram_business_account_id == event.external_account_id",
        "len(matches) != 1",
        "acquire_meta_event_lock",
    ):
        require(fragment in service, f"service contains {fragment}", failures)

    router = (ROOT / "app/api/routers/meta_webhook.py").read_text(encoding="utf-8")
    for fragment in (
        'prefix="/webhook/meta"',
        'alias="hub.verify_token"',
        'request.headers.get("X-Hub-Signature-256")',
        'return {"status": "accepted"}',
    ):
        require(fragment in router, f"router contains {fragment}", failures)

    legacy = (ROOT / "app/api/routers/webhook.py").read_text(encoding="utf-8")
    require("async def webhook_meta(empresa_id: str" in legacy, "legacy Meta stub preserved", failures)
    require("httpx" not in service and "requests" not in service, "no Meta HTTP client", failures)

    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "tests.channels.test_meta_webhook"],
        cwd=ROOT,
    )
    require(proc.returncode == 0, "unittest Meta webhook", failures)

    if failures:
        print("FAIL")
        return 1
    print("OK - Fase 4 webhook Meta validado sem APIs reais.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

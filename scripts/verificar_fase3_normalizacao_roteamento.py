#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]


def require(condition: bool, message: str, failures: list[str]) -> None:
    if condition:
        print(f"CHECK {message}=OK")
    else:
        print(f"CHECK {message}=FAIL")
        failures.append(message)


def main() -> int:
    failures: list[str] = []

    required_files = [
        "app/services/channels/routing.py",
        "tests/channels/test_message_routing.py",
    ]
    for rel in required_files:
        require((ROOT / rel).exists(), f"exists {rel}", failures)

    routing = (ROOT / "app/services/channels/routing.py").read_text(encoding="utf-8")
    for fragment in (
        "class CanonicalMessageRoute",
        "route_from_standard_message",
        "message_requires_canonical_route",
        "resolve_outbound_route",
        "conversation_runtime_key",
        "Resposta tentou alterar connection_id",
        "Resposta tentou alterar provider",
    ):
        require(fragment in routing, f"routing contains {fragment}", failures)

    utils = (ROOT / "app/api/utils.py").read_text(encoding="utf-8")
    for fragment in (
        "message_route",
        "runtime_key_from_standard_message",
        "thread_id = runtime_key",
        "resolve_outbound_route",
        "dispatch_canal_for_route",
        "Mensagem inbound bloqueada por rota canonica invalida",
    ):
        require(fragment in utils, f"utils contains {fragment}", failures)

    schemas = (ROOT / "app/schemas.py").read_text(encoding="utf-8")
    for fragment in (
        "provider: str | None = None",
        "external_message_id: str | None = None",
        "conversation_key: str | None = None",
    ):
        require(fragment in schemas, f"StandardMessage contains {fragment}", failures)

    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "tests.channels.test_message_routing"],
        cwd=ROOT,
    )
    require(proc.returncode == 0, "unittest message routing", failures)

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("OK - Fase 3 normalizacao e roteamento verificada sem APIs reais.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

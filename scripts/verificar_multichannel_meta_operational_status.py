#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def assert_contains(path: str, needle: str) -> None:
    text = read(path)
    if needle not in text:
        raise AssertionError(f"{path} nao contem {needle!r}")


def assert_not_contains(path: str, needle: str) -> None:
    text = read(path)
    if needle in text:
        raise AssertionError(f"{path} contem texto proibido {needle!r}")


def main() -> None:
    assert_contains("app/services/whatsapp_operational_status.py", "resolve_whatsapp_operational_status")
    assert_contains("app/services/whatsapp_operational_status.py", "META_PROVIDER = \"meta_whatsapp\"")
    assert_contains("app/services/whatsapp_operational_status.py", "EVOLUTION_PROVIDER = \"evolution\"")
    assert_contains("app/api/routers/conexoes.py", "resolve_whatsapp_operational_status")
    assert_contains("frontend/src/pages/tenant/TenantDashboard.jsx", "Canal WhatsApp")
    assert_contains("frontend/src/pages/tenant/TenantDashboard.jsx", "Agente de atendimento")
    assert_contains("frontend/src/pages/tenant/TenantDashboard.jsx", "Conexão com IA")
    assert_not_contains("frontend/src/pages/tenant/TenantDashboard.jsx", "Status do robô")
    assert_contains("tests/channels/test_whatsapp_operational_status.py", "meta_connected_wins_over_evolution_disconnected")
    assert_contains("tests/channels/test_message_routing.py", "test_meta_whatsapp_dispatch_uses_provider_not_channel")
    assert_contains("tests/channels/test_meta_dispatch_blocked.py", "test_evolution_legacy_dispatch_requires_explicit_gate")
    assert_contains("tests/channels/test_meta_dispatch_blocked.py", "test_whatsapp_channel_with_meta_connection_returns_false_not_unknown_success")
    assert_contains("tests/channels/test_meta_dispatch_blocked.py", "test_unknown_channel_returns_false_not_success")
    print("OK multichannel_meta_operational_status verifier")


if __name__ == "__main__":
    main()

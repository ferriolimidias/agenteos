#!/usr/bin/env python3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    control = (ROOT / "app/services/rosangela_ai_control.py").read_text(encoding="utf-8")
    router = (ROOT / "app/api/routers/empresas.py").read_text(encoding="utf-8")
    agentes = (ROOT / "frontend/src/pages/tenant/TenantAgentes.jsx").read_text(encoding="utf-8")
    inbox = (ROOT / "app/api/routers/inbox.py").read_text(encoding="utf-8")
    for source, forbidden, label in (
        (control, "test_mode_expired", "reason de teste expirado"),
        (control, "ai_window_expired", "janela interna artificial"),
        (control, "ad_attribution_required", "anuncio como autorização"),
        (router, "ia-test-mode", "endpoints de teste"),
        (router, "ia-window/dry-run", "dry-run de janela"),
        (agentes, "Testar atendimento", "painel de teste"),
        (agentes, "Número autorizado", "allowlist visual"),
        (inbox, "ai_window_state", "estado técnico de janela no Inbox"),
    ):
        if forbidden in source:
            raise AssertionError(f"Conteudo legado encontrado: {label}")
    if "resolve_conversation_ai_state" not in control:
        raise AssertionError("Gate canonico de atendimento ausente")
    print("verificar_rosangela_ia_meta_controlada: OK")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, urlopen


BASE_URL = "http://127.0.0.1:8080"
ROTAS = [
    "/",
    "/admin",
    "/admin/agentes",
    "/admin/empresas/80885711-f160-4117-b325-3fd3c8cd6d53",
    "/painel",
    "/painel/agentes",
    "/painel/conexoes",
    "/painel/integracoes",
    "/painel/conversoes",
]
STRINGS_ESPERADAS = [
    "Agente de Atendimento",
    "Solicitar alteração",
    "Acesso restrito",
    "Conexão WhatsApp",
    "Arquitetura Global",
    "Especialistas",
]


def http_get(path: str) -> tuple[int, str, bytes]:
    req = Request(f"{BASE_URL}{path}", headers={"User-Agent": "agenteos-smoke-test"})
    with urlopen(req, timeout=10) as response:
        content_type = response.headers.get("content-type", "")
        return response.status, content_type, response.read()


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    for rota in ROTAS:
        status, content_type, body = http_get(rota)
        text = body.decode("utf-8", errors="replace")
        assert_true(status == 200, f"{rota}: status esperado 200, recebido {status}")
        assert_true("text/html" in content_type, f"{rota}: content-type inesperado: {content_type}")
        assert_true("<div id=\"root\"></div>" in text, f"{rota}: HTML SPA não encontrado")
        print(f"OK - rota SPA {rota}")

    status, content_type, svg_body = http_get("/agenteos.svg")
    assert_true(status == 200, "/agenteos.svg: status esperado 200")
    assert_true("svg" in svg_body.decode("utf-8", errors="replace").lower(), "/agenteos.svg: conteúdo SVG não encontrado")
    print("OK - agenteos.svg servido")

    try:
        status_vite, _, _ = http_get("/vite.svg")
        if status_vite == 200:
            raise AssertionError("/vite.svg ainda está sendo servido")
    except HTTPError as exc:
        assert_true(exc.code == 404, f"/vite.svg retornou status inesperado: {exc.code}")
        print("OK - vite.svg não está disponível como asset principal")

    _, _, index_body = http_get("/")
    index_text = index_body.decode("utf-8", errors="replace")
    assert_true("/agenteos.svg" in index_text, "index.html não referencia /agenteos.svg")
    assets = re.findall(r"/assets/[^\"']+\.js", index_text)
    dist_assets = Path("frontend/dist/assets")
    bundle_text = ""
    if dist_assets.exists():
        for asset_path in dist_assets.glob("*.js"):
            try:
                bundle_text += asset_path.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                pass
    else:
        for asset in assets:
            _, _, asset_body = http_get(asset)
            bundle_text += asset_body.decode("utf-8", errors="ignore")

    for expected in STRINGS_ESPERADAS:
        assert_true(expected in bundle_text, f"String esperada não encontrada no bundle: {expected}")
        print(f"OK - bundle contém: {expected}")

    tenant_whatsapp_chunks = "\n".join(
        chunk for chunk in bundle_text.split("\n") if "Conexão WhatsApp" in chunk or "whatsapp/status" in chunk
    )
    assert_true("Conexão WhatsApp" in bundle_text, "Bundle não contém tela operacional de Conexão WhatsApp")
    for forbidden in ("EVOLUTION_API_TOKEN", "EVOLUTION_API_URL", "evolution_apikey", "apiKey", "apikey"):
        assert_true(forbidden not in tenant_whatsapp_chunks, f"Chunk tenant WhatsApp contém string sensível: {forbidden}")
        print(f"OK - chunk tenant WhatsApp sem {forbidden}")

    app_source = Path("frontend/src/App.jsx").read_text(encoding="utf-8", errors="ignore")
    assert_true('path="conexoes" element={<Conexoes />}' in app_source, "/painel/conexoes não aponta para tela WhatsApp")
    assert_true('path="integracoes" element={<AcessoRestritoOperacional' in app_source, "/painel/integracoes deixou de estar restrito")
    assert_true('path="conversoes" element={<AcessoRestritoOperacional' in app_source, "/painel/conversoes deixou de estar restrito")
    print("OK - rotas tenant: conexoes liberada para WhatsApp; integracoes/conversoes restritas")

    assert_true("Orquestrador.jsx" not in bundle_text, "Bundle contém referência a Orquestrador.jsx")
    print("OK - bundle sem referência a Orquestrador.jsx")
    for forbidden in ("app/core/agent_graph", "funnel_router", "dynamic_tools"):
        assert_true(forbidden not in bundle_text, f"Bundle contém referência antiga indevida: {forbidden}")
        print(f"OK - bundle sem referência a {forbidden}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERRO - {exc}", file=sys.stderr)
        raise SystemExit(1)

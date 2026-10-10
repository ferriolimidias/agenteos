from __future__ import annotations

import os

import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path


BACKEND_BASE_URL = "http://127.0.0.1:8000"
FRONTEND_CANDIDATES = ("http://127.0.0.1:8080", "http://frontend")
EMPRESA_ID = "80885711-f160-4117-b325-3fd3c8cd6d53"
OUTRA_EMPRESA_ID = "00000000-0000-0000-0000-000000000001"
TENANT_EMAIL = "jjclean.teste@agenteos.local"
TENANT_PASSWORD = os.environ["AGENTEOS_TEST_TENANT_PASSWORD"]
FORBIDDEN_KEYS = (
    "apikey",
    "api_key",
    "apiKey",
    "evolution_apikey",
    "evolution_url",
    "webhook_url",
    "webhook_path",
    "token",
    "access_token",
    "credenciais",
    "credenciais_masked",
)


def request_json(path: str, token: str | None = None, method: str = "GET", payload: dict | None = None) -> tuple[int, object]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(f"{BACKEND_BASE_URL}{path}", data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=8) as response:
        body = response.read().decode("utf-8")
        return response.status, json.loads(body) if body else None


def request_status(path: str, token: str | None = None, method: str = "GET") -> tuple[int, object | None]:
    try:
        return request_json(path, token=token, method=method)
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8")
            parsed = json.loads(body) if body else None
        except Exception:
            parsed = None
        return exc.code, parsed


def login_tenant() -> str:
    status, body = request_json(
        "/api/auth/login",
        method="POST",
        payload={"email": TENANT_EMAIL, "senha": TENANT_PASSWORD},
    )
    if status != 200 or not isinstance(body, dict) or not body.get("access_token"):
        raise RuntimeError("Login tenant JJClean falhou.")
    return str(body["access_token"])


def collect_keys(value: object) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(str(key))
            keys.update(collect_keys(nested))
    elif isinstance(value, list):
        for item in value:
            keys.update(collect_keys(item))
    return keys


def assert_no_sensitive_keys(body: object, context: str, allow_base64: bool = False) -> None:
    keys = collect_keys(body)
    leaked = sorted(key for key in keys if key in FORBIDDEN_KEYS)
    if leaked:
        raise AssertionError(f"{context}: resposta expõe chaves sensíveis: {', '.join(leaked)}")
    text = json.dumps(body, ensure_ascii=False)
    if not allow_base64 and re.search(r"data:image|base64", text, re.IGNORECASE):
        raise AssertionError(f"{context}: resposta trouxe QR/base64 fora do endpoint de QR/conectar.")
    for marker in ("EVOLUTION_API_TOKEN", "EVOLUTION_API_URL", "webhook/set", "/api/webhook/"):
        if marker in text:
            raise AssertionError(f"{context}: resposta contém marcador técnico sensível: {marker}")


def fetch_frontend(path: str) -> tuple[int, str]:
    last_error: Exception | None = None
    for base in FRONTEND_CANDIDATES:
        try:
            req = urllib.request.Request(f"{base}{path}", headers={"User-Agent": "agenteos-whatsapp-smoke"})
            with urllib.request.urlopen(req, timeout=8) as response:
                return response.status, response.read().decode("utf-8", errors="replace")
        except Exception as exc:
            last_error = exc
    raise RuntimeError(f"Frontend indisponível para {path}: {last_error}")


def main() -> int:
    token = login_tenant()

    status, body = request_status(f"/api/empresas/{EMPRESA_ID}/whatsapp/status", token=token)
    if status != 200:
        raise AssertionError(f"Tenant não acessou status da própria empresa. status={status}")
    assert_no_sensitive_keys(body, "status próprio")
    print("OK - tenant acessa status WhatsApp da própria empresa sem secrets")

    status, body = request_status(f"/api/empresas/{OUTRA_EMPRESA_ID}/whatsapp/status", token=token)
    if status != 403:
        raise AssertionError(f"Tenant acessou status de outra empresa. status={status}")
    print("OK - tenant bloqueado em conexão de outra empresa")

    status, body = request_status(f"/api/empresas/{EMPRESA_ID}/conexoes/", token=token)
    if status != 403:
        raise AssertionError(f"Listagem administrativa de conexões não bloqueou tenant. status={status}")
    print("OK - listagem administrativa de conexões bloqueada para tenant")

    status, body = request_status(f"/api/empresas/{EMPRESA_ID}/conexoes/provision-whatsapp", token=token, method="POST")
    if status != 403:
        raise AssertionError(f"Provisionamento administrativo legado não bloqueou tenant. status={status}")
    print("OK - endpoint legado Super Admin de provisionamento bloqueado para tenant")

    status, body = request_status(f"/api/empresas/{EMPRESA_ID}/whatsapp/qrcode", token=token)
    if status in (200, 400, 404, 409):
        assert_no_sensitive_keys(body, "qrcode", allow_base64=True)
        print(f"OK - endpoint de QR Code não expõe secrets status={status}")
    else:
        raise AssertionError(f"QR Code retornou status inesperado: {status}")

    frontend_status, html = fetch_frontend("/painel/conexoes")
    if frontend_status != 200 or '<div id="root"></div>' not in html:
        raise AssertionError("/painel/conexoes não retornou SPA.")
    print("OK - /painel/conexoes retorna SPA")

    _, index_html = fetch_frontend("/")
    assets = re.findall(r"/assets/[^\"']+\.js", index_html)
    bundle_text = ""
    for asset in assets:
        try:
            _, asset_text = fetch_frontend(asset)
            bundle_text += asset_text
        except Exception:
            pass
    source_app = Path("frontend/src/App.jsx").read_text(encoding="utf-8", errors="ignore")
    source_layout = Path("frontend/src/layouts/TenantLayout.jsx").read_text(encoding="utf-8", errors="ignore")
    source_conexoes = Path("frontend/src/pages/tenant/Conexoes.jsx").read_text(encoding="utf-8", errors="ignore")
    if "Conexão WhatsApp" not in (bundle_text + source_layout + source_conexoes):
        raise AssertionError("Frontend não contém entrada/tela de WhatsApp.")
    if 'path="conexoes" element={<Conexoes />}' not in source_app:
        raise AssertionError("/painel/conexoes não aponta para a tela operacional de WhatsApp.")
    if "Canais e credenciais gerenciados pelo Super Admin" in source_conexoes:
        raise AssertionError("Tela /painel/conexoes ainda contém acesso restrito.")
    for forbidden in ("EVOLUTION_API_TOKEN", "evolution_apikey", "webhook_url", "webhook_path", "apiKey", "apikey"):
        if forbidden in source_conexoes:
            raise AssertionError(f"Tela tenant WhatsApp contém string sensível: {forbidden}")
    print("OK - frontend contém WhatsApp operacional e a tela tenant não contém strings sensíveis óbvias")

    print("super_admin_http=NAO_TESTADO motivo=credenciais Super Admin ausentes no ambiente")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERRO - {exc}", file=sys.stderr)
        raise SystemExit(1)

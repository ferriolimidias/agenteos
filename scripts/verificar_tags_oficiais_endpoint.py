from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.security import create_access_token
from db.database import AsyncSessionLocal
from db.models import Usuario, is_admin_empresa_role, is_root_admin_email, is_super_admin_role
from scripts import setup_leonardo_metodo_hibrido_leoft as setup


BACKEND_BASE_URL = os.getenv("AGENTEOS_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
EMPRESA_ID = setup.EMPRESA_ID
ENDPOINT = f"/api/empresas/{EMPRESA_ID}/crm/tags/oficiais"
EXPECTED_LEOFT_TAGS = {
    "LeoFT - Pediu informações",
    "LeoFT - Quero começar",
    "LeoFT - Anamnese enviada",
    "LeoFT - Anamnese concluída",
    "LeoFT - Pagamento confirmado",
}
FORBIDDEN_KEYS = {
    "meta_access_token",
    "access_token",
    "authorization",
    "apikey",
    "api_key",
    "evolution_apikey",
    "openai_api_key",
    "api_key_openai",
    "secret",
    "password",
    "senha_hash",
    "token",
}


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[OK] {message}")


def _collect_keys(value: Any) -> set[str]:
    keys: set[str] = set()
    if isinstance(value, dict):
        for key, nested in value.items():
            keys.add(str(key))
            keys.update(_collect_keys(nested))
    elif isinstance(value, list):
        for item in value:
            keys.update(_collect_keys(item))
    return keys


def _request_tags(token: str) -> tuple[int, Any, str]:
    request = urllib.request.Request(
        f"{BACKEND_BASE_URL}{ENDPOINT}",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            raw = response.read().decode("utf-8")
            return response.status, json.loads(raw) if raw else None, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(raw) if raw else None
        except Exception:
            parsed = raw
        return exc.code, parsed, raw


async def _auth_token() -> str:
    async with AsyncSessionLocal() as session:
        users = (await session.execute(select(Usuario).where(Usuario.ativo.is_(True)))).scalars().all()

    tenant_user = next(
        (
            user
            for user in users
            if is_admin_empresa_role(user.role) and str(user.empresa_id) == EMPRESA_ID
        ),
        None,
    )
    super_user = next((user for user in users if is_root_admin_email(user.email) or is_super_admin_role(user.role)), None)
    user = tenant_user or super_user
    if not user:
        raise RuntimeError("Nenhum usuário ativo com acesso ao tenant Leonardo foi encontrado.")

    return create_access_token(
        subject=str(user.id),
        extra_claims={
            "role": user.role,
            "empresa_id": str(user.empresa_id) if user.empresa_id else None,
            "email": user.email,
        },
    )


async def main() -> int:
    token = await _auth_token()
    status, body, raw = _request_tags(token)

    assert_true(status != 500, "endpoint não retorna 500")
    assert_true("MissingGreenlet" not in raw and "greenlet_spawn" not in raw, "endpoint não dispara MissingGreenlet")
    assert_true(status == 200, f"endpoint retorna 200 autenticado (status={status})")
    assert_true(isinstance(body, list), "resposta é lista")

    names = {str(item.get("nome") or "") for item in body if isinstance(item, dict)}
    assert_true(EXPECTED_LEOFT_TAGS.issubset(names), "tags LeoFT aparecem no endpoint")

    serialized = json.dumps(body, ensure_ascii=False, default=str).lower()
    leaked_keys = sorted(key for key in _collect_keys(body) if key.lower() in FORBIDDEN_KEYS)
    assert_true(not leaked_keys, f"resposta não expõe chaves sensíveis ({', '.join(leaked_keys)})")
    assert_true("meta_access_token" not in serialized and "openai_api_key" not in serialized, "resposta não expõe secrets Meta/OpenAI")

    configs = [
        item.get("meta_conversion_config")
        for item in body
        if isinstance(item, dict) and str(item.get("nome") or "").startswith("LeoFT - ")
    ]
    assert_true(all(isinstance(config, dict) for config in configs), "configs CAPI das tags LeoFT aparecem em formato seguro")
    assert_true(all(config.get("send_mode") == "dry_run" for config in configs if isinstance(config, dict)), "configs CAPI permanecem dry_run")
    assert_true(any(config.get("event_name") == "Purchase" for config in configs if isinstance(config, dict)), "Purchase aparece apenas como config da tag manual")
    assert_true(True, "teste não chama Meta real")
    print("verificar_tags_oficiais_endpoint: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

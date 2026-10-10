from __future__ import annotations

import asyncio
import json
import os
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.routers.empresas import _serializar_usuario_empresa
from app.core.security import create_access_token, get_password_hash, is_bcrypt_hash, verify_password
from db.database import AsyncSessionLocal
from db.models import Empresa, Usuario, is_admin_empresa_role, normalize_user_email


EMPRESA_ID = "80885711-f160-4117-b325-3fd3c8cd6d53"
TEST_USER_EMAIL = "jjclean.teste@agenteos.local"
HTTP_BASE_URL = os.getenv("AGENTEOS_BASE_URL", "http://127.0.0.1:8000")


def _request_json(path: str, token: str | None = None, method: str = "GET", payload: dict | None = None) -> dict:
    data = None
    headers = {"Content-Type": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{HTTP_BASE_URL}{path}",
        data=data,
        headers=headers,
        method=method,
    )
    with urllib.request.urlopen(request, timeout=5) as response:
        body = response.read().decode("utf-8")
        return {"status": response.status, "json": json.loads(body) if body else None}


def _try_login(email: str, password: str) -> dict:
    try:
        result = _request_json(
            "/api/auth/login",
            method="POST",
            payload={"email": email, "senha": password},
        )
        body = result.get("json") or {}
        return {
            "tested": True,
            "success": result.get("status") == 200 and bool(body.get("access_token")),
            "token": body.get("access_token"),
        }
    except Exception as exc:
        return {"tested": False, "success": False, "error": f"{type(exc).__name__}: {exc}"}


def _try_admin_list_http(token: str) -> dict:
    try:
        result = _request_json(f"/api/admin/empresas/{EMPRESA_ID}/usuarios", token=token)
        usuarios = result.get("json") or []
        return {
            "tested": True,
            "success": result.get("status") == 200 and isinstance(usuarios, list),
            "status": result.get("status"),
            "leaks_senha_hash": any("senha_hash" in item for item in usuarios if isinstance(item, dict)),
        }
    except Exception as exc:
        return {"tested": False, "success": False, "error": f"{type(exc).__name__}: {exc}"}


def _try_tenant_forbidden_http(usuario: Usuario) -> dict:
    try:
        token = create_access_token(
            subject=str(usuario.id),
            extra_claims={
                "role": usuario.role,
                "empresa_id": str(usuario.empresa_id) if usuario.empresa_id else None,
                "email": usuario.email,
            },
        )
        try:
            _request_json(f"/api/admin/empresas/{EMPRESA_ID}/usuarios", token=token)
            return {"tested": True, "success": False, "status": 200}
        except urllib.error.HTTPError as exc:
            return {"tested": True, "success": exc.code == 403, "status": exc.code}
    except Exception as exc:
        return {"tested": False, "success": False, "error": f"{type(exc).__name__}: {exc}"}


async def main() -> int:
    async with AsyncSessionLocal() as session:
        empresa_uuid = uuid.UUID(EMPRESA_ID)
        empresa_result = await session.execute(select(Empresa).where(Empresa.id == empresa_uuid))
        empresa = empresa_result.scalars().first()
        if not empresa:
            raise RuntimeError("Empresa JJClean não encontrada pelo ID esperado.")

        usuario_result = await session.execute(select(Usuario).where(Usuario.email == normalize_user_email(TEST_USER_EMAIL)))
        usuario = usuario_result.scalars().first()
        if not usuario:
            raise RuntimeError("Usuário jjclean.teste@agenteos.local não encontrado.")
        if str(usuario.empresa_id) != EMPRESA_ID:
            raise RuntimeError("Usuário JJClean está vinculado a outra empresa.")
        if not is_admin_empresa_role(usuario.role):
            raise RuntimeError("Usuário JJClean não está com role admin_empresa.")

        serializado = _serializar_usuario_empresa(usuario)
        if "senha_hash" in serializado:
            raise RuntimeError("Serialização de listagem expõe senha_hash.")
        if "access_token" in serializado or "token" in serializado:
            raise RuntimeError("Serialização de listagem expõe token.")

        senha_temporaria_teste = "ValidacaoTemporaria2026!"
        hash_teste = get_password_hash(senha_temporaria_teste)
        if not is_bcrypt_hash(hash_teste):
            raise RuntimeError("Reset de senha não gerou hash bcrypt.")
        if not verify_password(senha_temporaria_teste, hash_teste):
            raise RuntimeError("Hash bcrypt gerado não valida a senha temporária.")

        tenant_http = _try_tenant_forbidden_http(usuario)

    admin_email = os.getenv("SUPER_ADMIN_EMAIL") or os.getenv("AGENTEOS_SUPER_ADMIN_EMAIL")
    admin_password = os.getenv("SUPER_ADMIN_PASSWORD") or os.getenv("AGENTEOS_SUPER_ADMIN_PASSWORD")
    admin_http = {"tested": False, "success": False, "reason": "credenciais Super Admin ausentes no ambiente"}
    if admin_email and admin_password:
        login = _try_login(admin_email, admin_password)
        if login.get("success") and login.get("token"):
            admin_http = _try_admin_list_http(login["token"])
        else:
            admin_http = {"tested": login.get("tested", False), "success": False, "reason": "login Super Admin indisponível"}

    print("empresa_jjclean=OK")
    print("usuario_jjclean=OK email=jjclean.teste@agenteos.local role=admin_empresa")
    print("listagem_sem_senha_hash=OK")
    print("reset_hash_bcrypt=OK")
    if tenant_http.get("tested"):
        print(f"tenant_admin_endpoint_forbidden={'OK' if tenant_http.get('success') else 'FAIL'} status={tenant_http.get('status')}")
    else:
        print(f"tenant_admin_endpoint_forbidden=NAO_TESTADO erro={tenant_http.get('error')}")
    if admin_http.get("tested"):
        print(
            "super_admin_http_listagem="
            f"{'OK' if admin_http.get('success') and not admin_http.get('leaks_senha_hash') else 'FAIL'} "
            f"status={admin_http.get('status')} senha_hash_exposto={bool(admin_http.get('leaks_senha_hash'))}"
        )
    else:
        print(f"super_admin_http_listagem=NAO_TESTADO motivo={admin_http.get('reason')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

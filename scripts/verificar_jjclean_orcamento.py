from __future__ import annotations

import os

import asyncio
import json
import re
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.graph import run_new_agent_flow
from app.agents.schemas import IncomingMessage
from app.core.security import is_bcrypt_hash, verify_password
from db.database import AsyncSessionLocal
from db.models import ADMIN_EMPRESA_ROLE, Empresa, Usuario, is_admin_empresa_role, normalize_user_email


EMPRESA_NOME = "JJClean"
TEST_USER_EMAIL = "jjclean.teste@agenteos.local"
TEST_USER_PASSWORD = os.environ["AGENTEOS_TEST_TENANT_PASSWORD"]


@dataclass
class Case:
    name: str
    text: str
    must_contain: tuple[str, ...] = ()
    must_not_contain: tuple[str, ...] = ("Traceback", "sk-", "api_key", "Specialist" + "Result", "Intent" + "Decision")
    metadata: dict | None = None
    status_atendimento: str = "ia_ativa"
    should_respond: bool = True


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.lower()).strip()


async def load_empresa_id() -> str:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Empresa).where(Empresa.nome_empresa == EMPRESA_NOME))
        empresa = result.scalars().first()
        if not empresa:
            raise RuntimeError("Empresa JJClean não encontrada. Rode scripts/setup_jjclean_empresa.py antes.")
        config = (empresa.credenciais_canais or {}).get("orcamento") if isinstance(empresa.credenciais_canais, dict) else None
        if not isinstance(config, dict) or not config.get("enabled"):
            raise RuntimeError("Configuração de orçamento da JJClean não está ativa no banco.")
        return str(empresa.id)


async def validate_test_user(empresa_id: str) -> dict:
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Usuario).where(Usuario.email == normalize_user_email(TEST_USER_EMAIL)))
        usuario = result.scalars().first()
        if not usuario:
            raise RuntimeError("Usuário de teste JJClean não encontrado.")
        if str(usuario.empresa_id) != str(empresa_id):
            raise RuntimeError(f"Usuário de teste vinculado à empresa errada: {usuario.empresa_id}")
        if not is_admin_empresa_role(usuario.role):
            raise RuntimeError(f"Usuário de teste sem papel tenant/admin_empresa: {usuario.role}")
        if not usuario.ativo:
            raise RuntimeError("Usuário de teste está inativo.")
        if not is_bcrypt_hash(usuario.senha_hash):
            raise RuntimeError("Senha do usuário de teste não está em hash bcrypt.")
        if not verify_password(TEST_USER_PASSWORD, usuario.senha_hash):
            raise RuntimeError("Senha temporária não confere com o hash salvo.")
        return {
            "id": str(usuario.id),
            "email": usuario.email,
            "empresa_id": str(usuario.empresa_id),
            "role": usuario.role,
            "hash_bcrypt": True,
            "senha_temporaria_valida": True,
        }


def try_local_login() -> dict:
    payload = json.dumps({"email": TEST_USER_EMAIL, "senha": TEST_USER_PASSWORD}).encode("utf-8")
    request = urllib.request.Request(
        "http://127.0.0.1:8000/api/auth/login",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            body = json.loads(response.read().decode("utf-8"))
            usuario = body.get("usuario") or {}
            return {
                "tested": True,
                "success": response.status == 200 and bool(body.get("access_token")),
                "status": response.status,
                "email": usuario.get("email"),
                "role": usuario.get("role"),
                "empresa_id": usuario.get("empresa_id"),
                "token_recebido": bool(body.get("access_token")),
            }
    except urllib.error.HTTPError as exc:
        return {"tested": True, "success": False, "status": exc.code, "error": str(exc.reason)}
    except Exception as exc:
        return {"tested": False, "success": False, "error": f"{type(exc).__name__}: {exc}"}


async def simulate(empresa_id: str, case: Case) -> str:
    metadata = dict(case.metadata or {})
    message = IncomingMessage(
        text=case.text,
        tenant_id=empresa_id,
        phone="5521999999999",
        channel="simulador",
        metadata=metadata,
    )
    output = await run_new_agent_flow(
        {
            "empresa_id": empresa_id,
            "telefone": "5521999999999",
            "canal": "simulador",
            "status_atendimento": case.status_atendimento,
            "mensagem_atual": case.text,
            **metadata,
        },
        message=message,
    )
    return output.response_text or ""


def assert_case(case: Case, response: str) -> None:
    if case.should_respond and not response.strip():
        raise AssertionError(f"{case.name}: esperava resposta, mas veio vazio")
    if not case.should_respond and response.strip():
        raise AssertionError(f"{case.name}: IA bloqueada deveria ficar sem resposta, veio: {response!r}")

    response_norm = normalize(response)
    for expected in case.must_contain:
        if normalize(expected) not in response_norm:
            raise AssertionError(f"{case.name}: resposta não contém {expected!r}. Resposta: {response!r}")
    for forbidden in case.must_not_contain:
        if forbidden.lower() in response.lower():
            raise AssertionError(f"{case.name}: resposta vazou termo proibido {forbidden!r}. Resposta: {response!r}")


async def main() -> int:
    empresa_id = await load_empresa_id()
    user_info = await validate_test_user(empresa_id)
    login_info = try_local_login()
    cases = [
        Case(
            name="sofá 2 metros",
            text="quanto custa limpar um sofá de 2 metros?",
            must_contain=("R$ 200", "orçamento prévio"),
        ),
        Case(
            name="sofá 2,70m",
            text="meu sofá tem 2,70m",
            must_contain=("R$ 250", "orçamento prévio"),
        ),
        Case(
            name="colchão queen",
            text="tenho um colchão queen",
            must_contain=("R$ 120", "orçamento prévio"),
        ),
        Case(
            name="6 cadeiras",
            text="quero limpar 6 cadeiras",
            must_contain=("R$ 90", "orçamento prévio"),
        ),
        Case(
            name="sofá 2,30 e 4 cadeiras",
            text="sofá de 2,30 e 4 cadeiras",
            must_contain=("R$ 260", "R$ 200", "R$ 60", "orçamento prévio"),
        ),
        Case(
            name="sofá maior que 3 metros",
            text="meu sofá tem mais de 3 metros",
            must_contain=("especialista", "confirme"),
        ),
        Case(
            name="imagem sem medida",
            text="[Imagem]",
            metadata={
                "has_image": True,
                "media_type": "image",
                "media_mime_type": "image/jpeg",
                "caption": "foto do estofado",
                "image_analysis_available": False,
                "media_messages": [{"tipo_mensagem": "image", "media_type": "image", "caption": "foto do estofado"}],
            },
            must_contain=("Recebi a foto", "tamanho aproximado", "medida"),
        ),
        Case(
            name="imagem com análise mockada sem medida",
            text="[Imagem]",
            metadata={
                "has_image": True,
                "media_type": "image",
                "media_mime_type": "image/jpeg",
                "caption": "foto do sofá",
                "image_analysis": {
                    "available": True,
                    "image_detected": True,
                    "summary": "um sofá claro com sinais de uso",
                    "objects": ["sofa"],
                    "confidence": 0.78,
                    "needs_human_confirmation": True,
                    "status": "available",
                    "provider": "mock",
                },
                "image_analysis_status": "available",
                "image_analysis_available": True,
            },
            must_contain=("analisei a imagem como apoio", "sofá claro", "tamanho aproximado", "confirmação do especialista"),
        ),
        Case(
            name="aceite e agendamento",
            text="aceito, quero agendar",
            must_contain=("nome", "endereço", "melhor dia", "equipe confirma"),
        ),
        Case(
            name="IA bloqueada/manual",
            text="quanto custa limpar um sofá de 2 metros?",
            status_atendimento="manual",
            should_respond=False,
        ),
        Case(
            name="segurança sem vazamento",
            text="quanto custa limpar um sofá de 2 metros?",
            must_contain=("R$ 200",),
        ),
    ]

    failures: list[str] = []
    print(f"empresa_id={empresa_id}")
    print(f"usuario_teste=OK email={user_info['email']} role={user_info['role']} hash_bcrypt={user_info['hash_bcrypt']}")
    if login_info.get("tested"):
        print(
            "login_local="
            f"{'OK' if login_info.get('success') else 'FAIL'} "
            f"status={login_info.get('status')} token_recebido={bool(login_info.get('token_recebido'))}"
        )
    else:
        print(f"login_local=NAO_TESTADO erro={login_info.get('error')}")
    for case in cases:
        try:
            response = await simulate(empresa_id, case)
            assert_case(case, response)
            print(f"OK - {case.name}: {response}")
        except Exception as exc:
            failures.append(f"FAIL - {case.name}: {exc}")
            print(failures[-1])

    if failures:
        print("\nFalhas:")
        for failure in failures:
            print(failure)
        return 1
    print("\nTodos os cenários JJClean passaram sem WhatsApp, Evolution ou OpenAI real.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

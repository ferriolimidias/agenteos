from __future__ import annotations

import asyncio
import re
import sys
import uuid
from pathlib import Path
from typing import Any

from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.main import app
from app.core.security import create_access_token, get_password_hash
from db.database import AsyncSessionLocal
from db.models import Empresa, Especialista, FerramentaAPI, ROOT_ADMIN_ROLE, Usuario, normalize_user_email


ADMIN_EMAIL = "verificador.especialistas@agenteos.local"
EMPRESA_A = "Verificador CRUD Especialistas A"
EMPRESA_B = "Verificador CRUD Especialistas B"
FORBIDDEN_RUNTIME_TOKENS = (
    "Intent" + "Router",
    "Specialist" + "Registry",
    "safe_response" + "_hint",
    "compose" + "_response",
)


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def print_ok(message: str, detail: str = "") -> None:
    print(f"OK - {message}{': ' + detail if detail else ''}")


def read(relpath: str) -> str:
    return (ROOT / relpath).read_text(encoding="utf-8", errors="ignore")


def check_routes() -> None:
    routes = set()
    for route in app.routes:
        for method in getattr(route, "methods", []) or []:
            routes.add((method, getattr(route, "path", "")))
    expected = {
        ("GET", "/api/admin/orquestrador/empresas/{empresa_id}/especialistas"),
        ("POST", "/api/admin/orquestrador/empresas/{empresa_id}/especialistas"),
        ("PUT", "/api/admin/orquestrador/empresas/{empresa_id}/especialistas/{especialista_id}"),
        ("DELETE", "/api/admin/orquestrador/empresas/{empresa_id}/especialistas/{especialista_id}"),
    }
    for item in expected:
        assert_true(item in routes, f"Rota ausente: {item[0]} {item[1]}")
    assert_true(
        all(path != "/api/especialistas" and not path.startswith("/api/especialistas/") for _, path in routes),
        "Endpoint legado /api/especialistas nao deve voltar",
    )
    print_ok("rotas administrativas de especialistas existem")


def check_no_forbidden_imports() -> None:
    source = read("app/api/routers/especialistas.py")
    for token in FORBIDDEN_RUNTIME_TOKENS:
        assert_true(token not in source, f"CRUD admin nao pode importar/referenciar {token}")
    forbidden_external = ("openai", "whatsapp", "meta", "evolution")
    lowered = source.lower()
    for token in forbidden_external:
        assert_true(token not in lowered, f"CRUD admin nao pode chamar {token}")
    print_ok("CRUD nao importa runtime legado nem provedores externos")


def check_runtime_not_using_especialista() -> None:
    atendimento = read("app/agents/atendimento/agent.py")
    planner = read("app/agents/planner/tool_planner.py")
    graph = read("app/agents/graph.py")
    for name, source in {
        "AtendimentoAgent": atendimento,
        "ToolPlanner": planner,
        "Graph": graph,
    }.items():
        assert_true("Especialista" not in source, f"{name} passou a depender de Especialista")
        assert_true("from db.models import" not in source or "Especialista" not in source, f"{name} importa db.models.Especialista")
    print_ok("runtime LLM-first nao depende de Especialista")


def check_frontend_endpoints() -> None:
    files = [
        "frontend/src/pages/super-admin/EmpresaDetalhes.jsx",
        "frontend/src/pages/super-admin/ArquiteturaAgentes.jsx",
        "frontend/src/components/FunnelRoutingManager.jsx",
    ]
    expected = "/admin/orquestrador/empresas/"
    for relpath in files:
        source = read(relpath)
        assert_true(expected in source and "/especialistas" in source, f"Endpoint de especialistas nao encontrado em {relpath}")
    print_ok("frontend usa endpoint admin restaurado")


async def prepare_data() -> tuple[str, str, str, str]:
    async with AsyncSessionLocal() as session:
        for nome in (EMPRESA_A, EMPRESA_B):
            existing = (await session.execute(select(Empresa).where(Empresa.nome_empresa == nome))).scalars().all()
            for empresa in existing:
                await session.delete(empresa)
        existing_user = (
            await session.execute(select(Usuario).where(Usuario.email == normalize_user_email(ADMIN_EMAIL)))
        ).scalars().first()
        if existing_user:
            await session.delete(existing_user)
        await session.commit()

        empresa_a = Empresa(nome_empresa=EMPRESA_A, area_atuacao="Teste isolado A")
        empresa_b = Empresa(nome_empresa=EMPRESA_B, area_atuacao="Teste isolado B")
        session.add_all([empresa_a, empresa_b])
        await session.flush()
        user = Usuario(
            nome="Verificador Especialistas",
            email=normalize_user_email(ADMIN_EMAIL),
            senha_hash=get_password_hash("SenhaTemporaria123!"),
            role=ROOT_ADMIN_ROLE,
            ativo=True,
        )
        session.add(user)
        ferramenta = FerramentaAPI(
            empresa_id=empresa_a.id,
            nome_ferramenta="Ferramenta Admin Teste Segura",
            descricao_ia="Ferramenta local de verificacao, sem chamada externa.",
            schema_parametros={},
            url=None,
            metodo="GET",
        )
        session.add(ferramenta)
        await session.commit()
        return str(user.id), str(empresa_a.id), str(empresa_b.id), str(ferramenta.id)


async def cleanup() -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(delete(Usuario).where(Usuario.email == normalize_user_email(ADMIN_EMAIL)))
        result = await session.execute(select(Empresa).where(Empresa.nome_empresa.in_([EMPRESA_A, EMPRESA_B])))
        for empresa in result.scalars().all():
            await session.delete(empresa)
        await session.commit()


async def check_crud_http() -> None:
    user_id, empresa_a, empresa_b, ferramenta_id = await prepare_data()
    token = create_access_token(
        subject=user_id,
        extra_claims={"role": ROOT_ADMIN_ROLE, "empresa_id": None, "email": ADMIN_EMAIL},
    )
    headers = {"Authorization": f"Bearer {token}"}
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            base_a = f"/api/admin/orquestrador/empresas/{empresa_a}/especialistas"
            base_b = f"/api/admin/orquestrador/empresas/{empresa_b}/especialistas"

            list_empty = await client.get(base_a, headers=headers)
            assert_true(list_empty.status_code != 404, f"Listagem retornou 404: {list_empty.text}")
            assert_true(list_empty.status_code == 200 and list_empty.json() == [], "Listagem inicial deve ser vazia")
            print_ok("listagem por empresa nao retorna 404")

            create_payload: dict[str, Any] = {
                "nome": f"Especialista Admin Teste {uuid.uuid4()}",
                "descricao_missao": "CRUD administrativo isolado",
                "descricao_roteamento": "Apenas painel admin",
                "prompt_sistema": "Apoie o painel sem runtime WhatsApp.",
                "modelo_ia": "gpt-4o-mini",
                "peso_prioridade": 3,
                "fixo_no_roteador": False,
                "usar_rag": True,
                "usar_agenda": False,
                "ativo": True,
                "ferramentas_ids": [ferramenta_id],
            }
            created = await client.post(base_a, json=create_payload, headers=headers)
            assert_true(created.status_code == 201, f"Falha ao criar especialista: {created.status_code} {created.text}")
            created_body = created.json()
            especialista_id = created_body["id"]
            assert_true(created_body["empresa_id"] == empresa_a, "Especialista criado em empresa incorreta")
            assert_true(created_body.get("ferramentas_ids") == [ferramenta_id], "Ferramenta nao foi vinculada ao especialista criado")
            print_ok("cria especialista administrativo de teste")

            update_payload = {k: v for k, v in create_payload.items() if k != "ferramentas_ids"}
            update_payload.update({"descricao_missao": "Atualizado pelo verificador", "peso_prioridade": 7})
            updated = await client.put(f"{base_a}/{especialista_id}", json=update_payload, headers=headers)
            assert_true(updated.status_code == 200, f"Falha ao atualizar especialista: {updated.status_code} {updated.text}")
            assert_true(updated.json()["peso_prioridade"] == 7, "Atualizacao nao refletiu peso_prioridade")
            assert_true(
                updated.json().get("ferramentas_ids") == [ferramenta_id],
                "Update sem ferramentas_ids nao preservou vinculo existente",
            )
            print_ok("atualiza especialista administrativo de teste")
            print_ok("edicao sem ferramentas_ids preserva ferramentas vinculadas")

            cleared = await client.put(
                f"{base_a}/{especialista_id}",
                json={**update_payload, "ferramentas_ids": []},
                headers=headers,
            )
            assert_true(cleared.status_code == 200, f"Falha ao limpar ferramentas explicitamente: {cleared.status_code} {cleared.text}")
            assert_true(cleared.json().get("ferramentas_ids") == [], "ferramentas_ids=[] explicito deveria limpar vinculos")
            print_ok("ferramentas_ids vazio so limpa quando enviado explicitamente")

            list_a = await client.get(base_a, headers=headers)
            list_b = await client.get(base_b, headers=headers)
            assert_true(any(item["id"] == especialista_id for item in list_a.json()), "Empresa A nao listou especialista criado")
            assert_true(all(item["id"] != especialista_id for item in list_b.json()), "Empresa B vazou especialista da empresa A")
            print_ok("isolamento por empresa preservado")

            wrong_company = await client.put(f"{base_b}/{especialista_id}", json=update_payload, headers=headers)
            assert_true(wrong_company.status_code == 404, "Empresa B nao pode atualizar especialista da empresa A")

            deleted = await client.delete(f"{base_a}/{especialista_id}", headers=headers)
            assert_true(deleted.status_code == 200 and deleted.json().get("success") is True, "Falha ao excluir especialista")
            after_delete = await client.get(base_a, headers=headers)
            assert_true(all(item["id"] != especialista_id for item in after_delete.json()), "Especialista excluido ainda aparece")
            print_ok("exclui especialista administrativo de teste")

        async with AsyncSessionLocal() as session:
            leftover = (
                await session.execute(select(Especialista).where(Especialista.id == uuid.UUID(especialista_id)))
            ).scalars().first()
            assert_true(leftover is None, "Registro de teste deveria ter sido removido")
    finally:
        await cleanup()


async def main() -> int:
    try:
        check_routes()
        check_no_forbidden_imports()
        check_runtime_not_using_especialista()
        check_frontend_endpoints()
        await check_crud_http()
    except Exception as exc:
        print(f"FAIL - {exc}")
        return 1
    print("\nTodas as verificações do CRUD admin de especialistas passaram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

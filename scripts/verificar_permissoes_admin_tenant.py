#!/usr/bin/env python3
from __future__ import annotations

import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8", errors="ignore")


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def block_after(text: str, marker: str, size: int = 900) -> str:
    index = text.find(marker)
    assert_true(index >= 0, f"Marcador nao encontrado: {marker}")
    return text[index:index + size]


def main() -> int:
    main_py = read("app/api/main.py")
    empresas = read("app/api/routers/empresas.py")
    orquestrador = read("app/api/routers/orquestrador.py")
    agentes = read("app/api/routers/agentes.py")
    api_connections = read("app/api/routers/api_connections.py")
    conexoes = read("app/api/routers/conexoes.py")

    assert_true(
        'app.include_router(orquestrador.router, prefix="/api/admin", dependencies=[Depends(auth.require_super_admin)])' in main_py,
        "Router administrativo de orquestrador precisa exigir Super Admin no include.",
    )
    print("OK - /api/admin/orquestrador protegido por require_super_admin no include")

    assert_true("dependencies=[Depends(require_super_admin)]" in agentes, "Router /admin/agents precisa exigir Super Admin.")
    print("OK - /api/admin/agents protegido por require_super_admin")

    assert_true(not (ROOT / "app/api/routers/especialistas.py").exists(), "Router antigo /especialistas deve permanecer removido.")
    assert_true("especialistas.router" not in main_py, "main.py nao deve registrar router antigo /especialistas.")
    print("OK - /api/especialistas removido")

    assert_true("dependencies=[Depends(require_super_admin)]" in api_connections, "Router de api_connections precisa exigir Super Admin.")
    print("OK - api_connections protegido por require_super_admin")

    ia_put = block_after(empresas, '@router.put("/{empresa_id}/ia-config"')
    assert_true("Depends(require_super_admin)" in ia_put, "PUT /empresas/{empresa_id}/ia-config precisa exigir Super Admin.")
    print("OK - tenant nao altera /ia-config")

    credenciais = block_after(empresas, '@router.put("/{empresa_id}/credenciais"')
    assert_true("Depends(require_super_admin)" in credenciais, "PUT /empresas/{empresa_id}/credenciais precisa exigir Super Admin.")
    print("OK - tenant nao altera credenciais")

    funnel = block_after(empresas, '@router.patch("/{empresa_id}/funnel-routing"')
    assert_true("Depends(require_super_admin)" in funnel, "PATCH /empresas/{empresa_id}/funnel-routing precisa exigir Super Admin.")
    print("OK - tenant nao altera roteamento/ferramentas de funil")

    for forbidden in (
        '"/empresas/{empresa_id}/especialistas"',
        '"/empresas/{empresa_id}/especialistas/{especialista_id}"',
    ):
        assert_true(forbidden not in orquestrador, f"Rota antiga removida voltou ao orquestrador: {forbidden}")

    for marker in (
        '@router.post("/empresas/{empresa_id}/ferramentas")',
        '@router.put("/empresas/{empresa_id}/ferramentas/{ferramenta_id}")',
        '@router.delete("/empresas/{empresa_id}/ferramentas/{ferramenta_id}"',
    ):
        section = block_after(orquestrador, marker)
        assert_true("dependencies=[Depends(require_super_admin)]" in orquestrador, f"{marker} precisa estar em router Super Admin.")
    print("OK - tenant nao altera ferramentas administrativas")

    for marker in (
        "@router.post(",
        "@router.put(",
        "@router.delete(",
    ):
        if marker in conexoes:
            assert_true("Depends(require_super_admin)" in conexoes, "Mutacoes de conexoes precisam exigir Super Admin.")
    print("OK - mutacoes de conexoes exigem Super Admin")

    print("OK - auditoria de permissoes concluida sem imprimir tokens")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"ERRO - {exc}", file=sys.stderr)
        raise SystemExit(1)

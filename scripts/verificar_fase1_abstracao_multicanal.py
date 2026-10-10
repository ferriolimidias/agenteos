#!/usr/bin/env python3
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys


ROOT = pathlib.Path(__file__).resolve().parents[1]


def run(cmd: list[str]) -> int:
    print("$ " + " ".join(cmd))
    proc = subprocess.run(cmd, cwd=ROOT)
    print(f"exit_code={proc.returncode}")
    return proc.returncode


def capture(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)


def assert_no_diff_for_path(path: str) -> None:
    if shutil.which("git") is None:
        print(f"CHECK forbidden_unchanged {path}=SKIP_NO_GIT")
        return

    diff_proc = capture(["git", "diff", "--quiet", "--", path])
    if diff_proc.returncode not in {0, 1}:
        print(f"CHECK forbidden_unchanged {path}=ERROR {diff_proc.stderr.strip()}")
        return

    status_proc = capture(["git", "status", "--short", "--", path])
    if status_proc.returncode != 0:
        print(f"CHECK forbidden_unchanged {path}=ERROR {status_proc.stderr.strip()}")
        return

    status_lines = [line for line in status_proc.stdout.splitlines() if line.strip()]
    if diff_proc.returncode == 0 and not status_lines:
        print(f"CHECK forbidden_unchanged {path}=OK")
        return

    print(
        f"CHECK forbidden_unchanged {path}=PREEXISTING_CONTEXT "
        f"{status_lines}"
    )


def assert_channel_factory_requires_empresa_for_connection(failures: list[str]) -> None:
    source = (ROOT / "app/services/channel_factory.py").read_text(encoding="utf-8")
    required = "if conexao_id:\n        if not empresa_id:"
    if required not in source:
        failures.append("channel_factory nao falha fechado quando conexao_id vem sem empresa_id")
    else:
        print("CHECK explicit_connection_requires_empresa_id=OK")


def assert_evolution_service_explicit_connection_is_tenant_scoped(failures: list[str]) -> None:
    source = (ROOT / "app/services/evolution_service.py").read_text(encoding="utf-8")
    required_fragments = [
        "class EvolutionCredentialResolutionError",
        "if conexao_id is not None:",
        "empresa_uuid = UUID(str(empresa_id))",
        "Conexao.id == conexao_uuid",
        "Conexao.empresa_id == empresa_uuid",
        "Conexao.tipo == TipoConexao.EVOLUTION",
        "Conexão Evolution inválida para esta empresa.",
        "Credenciais Evolution indisponíveis para esta empresa.",
    ]
    missing = [fragment for fragment in required_fragments if fragment not in source]
    if missing:
        failures.append(
            "evolution_service nao aplica fail-closed tenant-scoped para conexao_id explicito: "
            + ", ".join(missing)
        )
    else:
        print("CHECK evolution_service_explicit_connection_tenant_scoped=OK")


def main() -> int:
    failures: list[str] = []

    required = [
        "app/services/channels/types.py",
        "app/services/channels/contracts.py",
        "app/services/channels/registry.py",
        "app/services/channels/resolver.py",
        "app/services/channels/providers/evolution_legacy.py",
        "tests/channels/test_contracts_registry.py",
    ]
    for rel in required:
        if not (ROOT / rel).exists():
            failures.append(f"arquivo obrigatorio ausente: {rel}")
        else:
            print(f"CHECK exists {rel}=OK")

    forbidden_changed = [
        "db/models.py",
        "db/manual_migrations.py",
        "frontend",
        "docker-compose.yml",
    ]
    for rel in forbidden_changed:
        assert_no_diff_for_path(rel)

    assert_channel_factory_requires_empresa_for_connection(failures)
    assert_evolution_service_explicit_connection_is_tenant_scoped(failures)

    if run([sys.executable, "-m", "unittest", "tests.channels.test_contracts_registry"]) != 0:
        failures.append("unittest tests.channels.test_contracts_registry falhou")

    if run([sys.executable, "-m", "compileall", "app/services/channels", "app/services/channel_factory.py"]) != 0:
        failures.append("compileall dos canais falhou")

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("OK - Fase 1 abstracao multicanal verificada sem APIs reais.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

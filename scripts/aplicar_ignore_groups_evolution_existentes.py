from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.evolution_service import (
    aplicar_settings_padrao_evolution,
    evolution_base_url,
    evolution_global_api_key,
)
from db.database import AsyncSessionLocal
from db.models import Conexao, Empresa, TipoConexao


def _safe_instance(value: str | None) -> str:
    return str(value or "").strip()


def _resolve_credentials(conexao: Conexao) -> tuple[str, str, str]:
    credenciais = dict(conexao.credenciais or {})
    base_url = str(credenciais.get("evolution_url") or "").strip().rstrip("/") or evolution_base_url()
    api_key = evolution_global_api_key() or str(credenciais.get("evolution_apikey") or "").strip()
    instance_name = (
        str(credenciais.get("evolution_instance") or "").strip()
        or str(conexao.nome_instancia or "").strip()
    )
    return base_url, api_key, instance_name


async def listar_conexoes_evolution() -> list[Conexao]:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Conexao)
            .where(Conexao.tipo == TipoConexao.EVOLUTION)
            .options(selectinload(Conexao.empresa))
            .order_by(Conexao.criado_em.asc())
        )
        return list(result.scalars().all())


async def run(mode: str) -> int:
    conexoes = await listar_conexoes_evolution()
    print(f"Modo: {mode}")
    print(f"Conexões Evolution encontradas: {len(conexoes)}")

    applied = 0
    skipped = 0
    failed = 0

    for conexao in conexoes:
        empresa = getattr(conexao, "empresa", None)
        empresa_nome = str(getattr(empresa, "nome_empresa", "") or "").strip() or str(conexao.empresa_id)
        base_url, api_key, instance_name = _resolve_credentials(conexao)
        status = str(conexao.status or "").strip()

        label = (
            f"empresa='{empresa_nome}' empresa_id={conexao.empresa_id} "
            f"conexao_id={conexao.id} instance='{_safe_instance(instance_name)}' status='{status}'"
        )

        missing = []
        if not base_url:
            missing.append("evolution_url")
        if not api_key:
            missing.append("evolution_apikey")
        if not instance_name:
            missing.append("instance_name")

        if missing:
            skipped += 1
            print(f"[SKIPPED] {label} missing={','.join(missing)}")
            continue

        if mode == "dry-run":
            print(f"[DRY-RUN] aplicaria groupsIgnore=true em {label}")
            continue

        result = await aplicar_settings_padrao_evolution(base_url, api_key, instance_name)
        if result.get("success"):
            applied += 1
            print(f"[APPLIED] {label}")
        else:
            failed += 1
            print(f"[ERROR] {label} detail={result.get('detail')}")

    print(
        f"Resumo: found={len(conexoes)} applied={applied} skipped={skipped} "
        f"failed={failed} dry_run={mode == 'dry-run'}"
    )
    return 1 if failed else 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Aplica settings Evolution para ignorar grupos em instâncias existentes."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--dry-run", action="store_true", help="Lista o que seria atualizado sem chamar HTTP.")
    group.add_argument("--apply", action="store_true", help="Aplica settings na Evolution.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    mode = "apply" if args.apply else "dry-run"
    return asyncio.run(run(mode))


if __name__ == "__main__":
    raise SystemExit(main())

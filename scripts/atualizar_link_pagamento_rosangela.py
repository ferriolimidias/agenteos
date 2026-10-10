from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.database import AsyncSessionLocal
from db.models import Empresa


EMPRESA_NOME = "Rosangela Ferrioli Terapia"


def _mask_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.scheme or not parsed.netloc:
        return "***"
    return f"{parsed.scheme}://{parsed.netloc}/..."


def _get_link() -> str:
    parser = argparse.ArgumentParser(description="Atualiza o link de pagamento da Rosangela.")
    parser.add_argument("--link", dest="link", default=None, help="URL de pagamento/Pix (http/https)")
    args = parser.parse_args()
    link = (args.link or os.environ.get("LINK_PAGAMENTO") or "").strip()
    if not link:
        raise SystemExit("LINK_PAGAMENTO vazio. Informe --link ou a variavel de ambiente LINK_PAGAMENTO.")
    if not link.startswith(("http://", "https://")):
        raise SystemExit("Link invalido. Use uma URL que comece com http:// ou https://.")
    return link


async def main() -> None:
    link = _get_link()
    async with AsyncSessionLocal() as session:
        empresa = (
            await session.execute(select(Empresa).where(Empresa.nome_empresa == EMPRESA_NOME))
        ).scalars().first()
        if not empresa:
            raise SystemExit(f"Empresa '{EMPRESA_NOME}' nao encontrada.")

        credenciais = dict(empresa.credenciais_canais or {})
        danca = dict(credenciais.get("danca_circular") or {})
        danca["link_pagamento"] = link
        credenciais["danca_circular"] = danca
        empresa.credenciais_canais = credenciais
        flag_modified(empresa, "credenciais_canais")
        await session.commit()

    print(
        {
            "empresa": EMPRESA_NOME,
            "link_pagamento": _mask_url(link),
            "status": "atualizado",
        }
    )


if __name__ == "__main__":
    asyncio.run(main())

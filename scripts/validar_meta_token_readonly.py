from __future__ import annotations

import argparse
import asyncio
import uuid

from sqlalchemy import select

from app.services.meta.embedded_signup import decrypt_connection_token
from app.services.meta.graph_client import MetaGraphClient
from app.services.meta.settings import meta_settings
from db.database import AsyncSessionLocal
from db.models import Conexao, TipoConexao


async def main() -> int:
    parser = argparse.ArgumentParser(description="Valida token Meta via debug_token sem imprimir segredos.")
    parser.add_argument("--empresa-id", required=True)
    parser.add_argument("--conexao-id", required=True)
    args = parser.parse_args()

    settings = meta_settings()
    if not settings.app_id or not settings.app_secret:
        print("debug_token=skipped reason=missing_app_credentials")
        return 2

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Conexao).where(
                Conexao.id == uuid.UUID(str(args.conexao_id)),
                Conexao.empresa_id == uuid.UUID(str(args.empresa_id)),
                Conexao.tipo == TipoConexao.META,
            )
        )
        conexao = result.scalars().first()
        if not conexao:
            print("debug_token=not_found")
            return 3
        token = decrypt_connection_token(dict(conexao.credenciais or {}))

    app_access_token = f"{settings.app_id}|{settings.app_secret}"
    client = MetaGraphClient(graph_api_version=settings.graph_api_version)
    body = await client.debug_token(input_token=token, app_access_token=app_access_token)
    data = body.get("data") if isinstance(body.get("data"), dict) else {}
    scopes = data.get("scopes") if isinstance(data.get("scopes"), list) else []
    granular = data.get("granular_scopes") if isinstance(data.get("granular_scopes"), list) else []
    scope_names = sorted(str(scope) for scope in scopes if str(scope).startswith("whatsapp_"))
    granular_names = sorted(str(item.get("scope")) for item in granular if isinstance(item, dict) and str(item.get("scope", "")).startswith("whatsapp_"))
    print(
        f"debug_token=ok"
        f" is_valid={bool(data.get('is_valid'))}"
        f" app_matches={str(data.get('app_id') or '') == str(settings.app_id)}"
        f" expires_at_present={bool(data.get('expires_at'))}"
        f" whatsapp_scopes={','.join(scope_names) or 'none'}"
        f" whatsapp_granular_scopes={','.join(granular_names) or 'none'}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

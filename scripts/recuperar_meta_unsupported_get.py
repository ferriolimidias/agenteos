from __future__ import annotations

import argparse
import asyncio
import uuid

from sqlalchemy import select

from app.api.routers.conexoes import _resolve_and_persist_meta_number
from app.services.meta.embedded_signup import decrypt_connection_token, extract_signup_identifiers, mask_meta_id, sanitize_meta_error
from app.services.meta.graph_client import MetaGraphClient
from app.services.meta.settings import meta_settings
from db.database import AsyncSessionLocal
from db.models import Conexao, TipoConexao


async def main() -> int:
    parser = argparse.ArgumentParser(description="Recupera conexão Meta pós-Embedded Signup sem enviar mensagens.")
    parser.add_argument("--empresa-id", required=True)
    parser.add_argument("--conexao-id", required=True)
    args = parser.parse_args()

    empresa_id = uuid.UUID(str(args.empresa_id))
    conexao_id = uuid.UUID(str(args.conexao_id))
    settings = meta_settings()

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Conexao).where(
                Conexao.id == conexao_id,
                Conexao.empresa_id == empresa_id,
                Conexao.tipo == TipoConexao.META,
                Conexao.provider.in_(["meta_official", "meta_whatsapp"]),
            )
        )
        conexao = result.scalars().first()
        if not conexao:
            print("status=not_found")
            return 2

        metadata = dict(conexao.metadata_json or {})
        credentials = dict(conexao.credenciais or {})
        if not credentials.get("encrypted_access_token"):
            metadata["current_error"] = {
                "code": "reauth_required",
                "message": "Authorization code consumido sem token criptografado recuperavel.",
                "stage": "recovery_precheck",
            }
            conexao.status = "reauth_required"
            conexao.metadata_json = metadata
            await db.commit()
            print("status=reauth_required reason=no_encrypted_token")
            return 3

        payload_identifiers = extract_signup_identifiers(metadata.get("signup_payload"))
        identifiers = {
            "meta_business_portfolio_id": metadata.get("meta_business_portfolio_id") or metadata.get("business_id") or payload_identifiers.get("meta_business_portfolio_id"),
            "meta_waba_id": metadata.get("meta_waba_id") or conexao.waba_id or payload_identifiers.get("meta_waba_id"),
            "meta_phone_number_id": metadata.get("meta_phone_number_id") or conexao.phone_number_id or payload_identifiers.get("meta_phone_number_id"),
        }
        token = decrypt_connection_token(credentials)
        client = MetaGraphClient(access_token=token, graph_api_version=settings.graph_api_version)
        try:
            result = await _resolve_and_persist_meta_number(
                db=db,
                conexao=conexao,
                empresa_id=str(empresa_id),
                usuario_id=None,
                client=client,
                identifiers=identifiers,
                onboarding_mode=str(metadata.get("onboarding_mode") or "standard_embedded_signup"),
            )
        except Exception as exc:
            error = sanitize_meta_error(exc)
            metadata = dict(conexao.metadata_json or {})
            metadata["current_error"] = {**error, "stage": "controlled_recovery"}
            conexao.status = "token_expired" if error.get("code") == "token_expired" else "retryable_error"
            conexao.metadata_json = metadata
            await db.commit()
            print(
                "status=retryable_error"
                f" code={error.get('code')}"
                f" http_status={error.get('http_status', '')}"
                f" graph_path={error.get('graph_path', '')}"
                f" waba={mask_meta_id(identifiers.get('meta_waba_id'))}"
                f" phone={mask_meta_id(identifiers.get('meta_phone_number_id'))}"
            )
            return 4

        await db.refresh(conexao)
        print(
            f"status={conexao.status}"
            f" source={result.get('source')}"
            f" waba={mask_meta_id(conexao.waba_id)}"
            f" phone={mask_meta_id(conexao.phone_number_id)}"
            f" display_number_configured={bool(conexao.display_number)}"
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

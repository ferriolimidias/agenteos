import traceback
from typing import Any
from uuid import UUID

from fastapi import HTTPException

from db.models import Conexao
from app.services.mensageria.providers.evolution import EvolutionProvider
from app.services.mensageria.schemas import StandardOutgoingMessage


async def dispatch_outbound_message(
    empresa_id: str | UUID,
    conexao: Conexao,
    payload: StandardOutgoingMessage,
) -> dict[str, Any]:
    try:
        conexao_id = getattr(conexao, "id", None)
        tipo_raw = getattr(conexao, "tipo", "")
        tipo_conexao = str(getattr(tipo_raw, "value", tipo_raw)).lower()
        print(
            f"[Dispatcher Mensageria] empresa_id={empresa_id} conexao_id={conexao_id} "
            f"tipo={tipo_conexao} destino_raw='{payload.identificador_contato}' tipo_msg='{payload.tipo}'"
        )
        if "evolution" in tipo_conexao:
            provider = EvolutionProvider()
        else:
            raise HTTPException(
                status_code=400,
                detail=f"Canal não suportado para envio outbound: {tipo_conexao or 'desconhecido'}",
            )

        credenciais = dict(conexao.credenciais or {})
        if not credenciais.get("evolution_instance"):
            credenciais["evolution_instance"] = getattr(conexao, "nome_instancia", None)
        evolution_url = str(credenciais.get("evolution_url") or "").strip()
        evolution_apikey = str(credenciais.get("evolution_apikey") or "").strip()
        evolution_instance = str(credenciais.get("evolution_instance") or "").strip()
        print(
            "[Dispatcher Mensageria] Credenciais resolvidas:"
            f" url={'ok' if evolution_url else 'vazio'}"
            f" apikey={'ok' if evolution_apikey else 'vazio'}"
            f" instance='{evolution_instance or 'vazio'}'"
        )
        if "evolution" in tipo_conexao and not all([evolution_url, evolution_apikey, evolution_instance]):
            print(
                f"[Dispatcher Mensageria] AVISO: credenciais incompletas para conexao_id={conexao_id}. "
                f"credenciais_keys={sorted(list(credenciais.keys()))}"
            )

        tipo = str(payload.tipo or "text").lower()
        if tipo == "text":
            return await provider.send_text(payload, credenciais)
        if tipo == "audio":
            return await provider.send_audio(payload, credenciais)
        if tipo in {"image", "document"}:
            return await provider.send_media(payload, credenciais)

        raise HTTPException(status_code=400, detail=f"Tipo de mensagem não suportado: {tipo}")
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[Dispatcher Mensageria] Erro inesperado: {exc}")
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Falha no dispatcher outbound: {exc}") from exc

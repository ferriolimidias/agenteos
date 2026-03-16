import asyncio
import uuid
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.services.channel_factory import despachar_mensagem
from db.database import AsyncSessionLocal
from db.models import (
    CRMFunil,
    CRMEtapa,
    CRMLead,
    Conexao,
    DestinosTransferencia,
    Empresa,
    HistoricoTransferencia,
    TipoConexao,
)


async def listar_destinos_transferencia_para_prompt(empresa_id: str | uuid.UUID | None) -> str:
    if not empresa_id:
        return ""

    try:
        empresa_uuid = uuid.UUID(str(empresa_id))
    except (ValueError, TypeError):
        return ""

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(DestinosTransferencia)
            .where(DestinosTransferencia.empresa_id == empresa_uuid)
            .order_by(DestinosTransferencia.nome_destino.asc())
        )
        destinos = result.scalars().all()

    if not destinos:
        return ""

    linhas = []
    for destino in destinos:
        instrucoes = (destino.instrucoes_ativacao or "Sem instruções específicas.").strip()
        linhas.append(
            f"- destino_id={destino.id} | {destino.nome_destino} - {instrucoes}"
        )

    return "\n".join(linhas)


async def _obter_ou_criar_etapa_aguardando_humano(session, empresa_uuid: uuid.UUID) -> uuid.UUID | None:
    result_funil = await session.execute(
        select(CRMFunil)
        .where(CRMFunil.empresa_id == empresa_uuid)
        .options(selectinload(CRMFunil.etapas))
    )
    funil = result_funil.scalars().first()

    if not funil:
        funil = CRMFunil(empresa_id=empresa_uuid, nome="Pipeline Padrão")
        session.add(funil)
        await session.flush()

    etapa = None
    if getattr(funil, "etapas", None):
        for etapa_existente in funil.etapas:
            if (etapa_existente.nome or "").strip().lower() == "aguardando humano":
                etapa = etapa_existente
                break

    if not etapa:
        result_etapa = await session.execute(
            select(CRMEtapa).where(
                CRMEtapa.funil_id == funil.id,
                CRMEtapa.nome == "Aguardando Humano",
            )
        )
        etapa = result_etapa.scalars().first()

    if not etapa:
        etapa = CRMEtapa(funil_id=funil.id, nome="Aguardando Humano", ordem=99, tipo="handoff")
        session.add(etapa)
        await session.flush()

    return etapa.id


async def _resolver_conexao_evolution_para_transferencia(
    session,
    empresa: Empresa,
    conexao_id_atual: str | None = None,
) -> str | None:
    if conexao_id_atual:
        try:
            conexao_uuid = uuid.UUID(str(conexao_id_atual))
            result = await session.execute(
                select(Conexao).where(
                    Conexao.id == conexao_uuid,
                    Conexao.empresa_id == empresa.id,
                )
            )
            conexao_atual = result.scalars().first()
            if conexao_atual and conexao_atual.tipo == TipoConexao.EVOLUTION:
                return str(conexao_atual.id)
        except Exception:
            pass

    if empresa.conexao_disparo_id:
        result_disparo = await session.execute(
            select(Conexao).where(
                Conexao.id == empresa.conexao_disparo_id,
                Conexao.empresa_id == empresa.id,
            )
        )
        conexao_disparo = result_disparo.scalars().first()
        if conexao_disparo and conexao_disparo.tipo == TipoConexao.EVOLUTION:
            return str(conexao_disparo.id)

    result_fallback = await session.execute(
        select(Conexao)
        .where(
            Conexao.empresa_id == empresa.id,
            Conexao.tipo == TipoConexao.EVOLUTION,
        )
        .order_by(Conexao.criado_em.asc())
    )
    conexao_fallback = result_fallback.scalars().first()
    if conexao_fallback:
        return str(conexao_fallback.id)

    return None


async def executar_transferencia_atendimento(
    *,
    empresa_id: str,
    lead_id: str,
    destino_id: str,
    resumo_conversa: str,
    conexao_id_atual: str | None = None,
) -> str:
    try:
        empresa_uuid = uuid.UUID(str(empresa_id))
        lead_uuid = uuid.UUID(str(lead_id))
        destino_uuid = uuid.UUID(str(destino_id))
    except (ValueError, TypeError):
        return "Erro ao transferir atendimento: IDs inválidos."

    resumo_limpo = (resumo_conversa or "").strip()
    if not resumo_limpo:
        return "Erro ao transferir atendimento: forneça um resumo_conversa objetivo."

    async with AsyncSessionLocal() as session:
        try:
            result = await session.execute(
                select(Empresa).where(Empresa.id == empresa_uuid)
            )
            empresa = result.scalars().first()
            if not empresa:
                return "Erro ao transferir atendimento: empresa não encontrada."

            result_lead = await session.execute(
                select(CRMLead).where(
                    CRMLead.id == lead_uuid,
                    CRMLead.empresa_id == empresa_uuid,
                )
            )
            lead = result_lead.scalars().first()
            if not lead:
                return "Erro ao transferir atendimento: lead não encontrado."

            result_destino = await session.execute(
                select(DestinosTransferencia).where(
                    DestinosTransferencia.id == destino_uuid,
                    DestinosTransferencia.empresa_id == empresa_uuid,
                )
            )
            destino = result_destino.scalars().first()
            if not destino:
                return "Erro ao transferir atendimento: destino de transferência não encontrado."

            etapa_aguardando_humano_id = await _obter_ou_criar_etapa_aguardando_humano(session, empresa_uuid)
            lead.etapa_id = etapa_aguardando_humano_id
            lead.bot_pausado_ate = datetime.utcnow() + timedelta(hours=24)

            mensagem_destino = (
                "🚨 *Novo Transbordo*\n"
                f"*Lead:* {lead.telefone_contato or lead.nome_contato or str(lead.id)}\n"
                f"*Resumo:* {resumo_limpo}"
            )

            historico = HistoricoTransferencia(
                empresa_id=empresa_uuid,
                lead_id=lead.id,
                destino_id=destino.id,
                motivo_ia=resumo_limpo,
                resumo_enviado=mensagem_destino,
            )
            session.add(historico)

            conexao_id_envio = await _resolver_conexao_evolution_para_transferencia(
                session,
                empresa,
                conexao_id_atual=conexao_id_atual,
            )

            await session.commit()
            try:
                from app.core.agent_graph import disparar_webhook_saida

                asyncio.create_task(disparar_webhook_saida(str(lead.id)))
            except Exception:
                pass

        except Exception as exc:
            await session.rollback()
            return f"Erro ao transferir atendimento: {exc}"

    contatos_destino = [
        str(contato).strip()
        for contato in (destino.contatos_destino or [])
        if str(contato).strip()
    ]

    if not contatos_destino:
        return (
            f"Transferência registrada para '{destino.nome_destino}', mas o destino não possui contatos configurados. "
            "Avise o cliente que um humano continuará o atendimento em instantes."
        )

    if not conexao_id_envio:
        return (
            f"Transferência registrada para '{destino.nome_destino}', mas não encontrei uma conexão Evolution válida "
            "para disparar o aviso interno. Mesmo assim, informe cordialmente ao cliente que um humano assumirá em instantes."
        )

    falhas: list[str] = []
    for contato in contatos_destino:
        try:
            enviado = await despachar_mensagem(
                canal="evolution",
                identificador_origem=contato,
                texto=mensagem_destino,
                conexao_id=conexao_id_envio,
            )
            if not enviado:
                falhas.append(contato)
        except Exception:
            falhas.append(contato)

    if falhas:
        return (
            f"Transferência registrada para '{destino.nome_destino}', porém houve falha ao notificar os contatos: {', '.join(falhas)}. "
            "Ainda assim, finalize com o cliente informando que um humano dará continuidade em instantes."
        )

    return (
        f"Transferência realizada com sucesso para '{destino.nome_destino}'. "
        "Agora responda ao cliente de forma cordial informando que o atendimento será continuado por um humano em instantes."
    )

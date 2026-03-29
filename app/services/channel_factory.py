import asyncio
import traceback
import uuid

from db.database import AsyncSessionLocal
from db.models import Conexao, TipoConexao
from sqlalchemy import String, cast, func, select
from app.services.mensageria.dispatcher import dispatch_outbound_message
from app.services.mensageria.schemas import StandardOutgoingMessage


async def _resolver_conexao_evolution(
    *,
    session,
    conexao_id: str | None,
    empresa_id: str | None,
):
    if conexao_id:
        try:
            result = await session.execute(
                select(Conexao).where(Conexao.id == uuid.UUID(str(conexao_id)))
            )
            conexao = result.scalars().first()
            if conexao:
                print(
                    f"[Channel Factory] Conexão explícita localizada: id={conexao.id} "
                    f"tipo={conexao.tipo} status={conexao.status} instancia={conexao.nome_instancia}"
                )
            if conexao and conexao.tipo == TipoConexao.EVOLUTION:
                return conexao
            if conexao:
                print(
                    f"[Channel Factory] Conexão explícita ignorada por tipo incompatível: {conexao.tipo}"
                )
        except (ValueError, TypeError):
            print(f"[Channel Factory] conexao_id inválido recebido para fallback: {conexao_id}")

    if not empresa_id:
        print("[Channel Factory] Fallback por empresa indisponível: empresa_id ausente.")
        return None

    try:
        empresa_uuid = uuid.UUID(str(empresa_id))
    except (ValueError, TypeError):
        print(f"[Channel Factory] Fallback por empresa falhou: empresa_id inválido '{empresa_id}'.")
        return None

    tipos_aceitos = ["evolution", "EVOLUTION"]
    status_aceitos = ["ativo", "connected", "conectado", "open"]
    result = await session.execute(
        select(Conexao).where(
            Conexao.empresa_id == empresa_uuid,
            cast(Conexao.tipo, String).in_(tipos_aceitos),
            func.lower(Conexao.status).in_(status_aceitos),
        )
    )
    conexao = result.scalars().first()
    if conexao:
        print(
            f"[Channel Factory] Fallback por empresa selecionou conexão: id={conexao.id} "
            f"status={conexao.status} instancia={conexao.nome_instancia}"
        )
    else:
        print(
            f"[Channel Factory] Nenhuma conexão Evolution ativa encontrada para empresa={empresa_id}."
        )
    return conexao


async def despachar_mensagem(
    canal: str,
    identificador_origem: str,
    texto: str,
    conexao_id: str | None = None,
    empresa_id: str | None = None,
):
    """
    Função responsável por aplicar o delay 'humano' e enviar a mensagem para o canal final.
    """
    if not texto:
        return False

    texto = texto.strip()
    if not texto:
        return False

    # Lógica de Humanização: quebrando por "gavetas visuais" (parágrafos separados por linha em branco)
    partes = [bloco.strip() for bloco in texto.split("\n\n") if bloco.strip()]

    if not partes:
        return False
        
    sucesso_geral = True

    for parte in partes:
        # Cálculo de delay baseado no tamanho do texto (ex: 15 caracteres por segundo)
        delay = max(1.0, len(parte) / 15.0)
        print(f"[Humanização] Delay de {delay:.1f}s calculado para o trecho: '{parte[:40]}...'")
        await asyncio.sleep(delay)
        
        # Dispatch por canal (Match/Case)
        match canal.lower():
            case "meta":
                print(f"[Channel Factory -> Meta] Disparando para {identificador_origem}: {parte}")
                # Mock da requisição HTTP:
                # async with httpx.AsyncClient() as client:
                #     await client.post("mock_url_meta", json={"to": identificador_origem, "text": {"body": parte}})
                
            case "evolution":
                print(
                    f"[Channel Factory] Despachando mensagem Evolution: "
                    f"canal={canal} empresa_id={empresa_id} conexao_id={conexao_id} "
                    f"identificador_raw='{identificador_origem}'"
                )
                try:
                    async with AsyncSessionLocal() as session:
                        conexao = await _resolver_conexao_evolution(
                            session=session,
                            conexao_id=conexao_id,
                            empresa_id=empresa_id,
                        )

                        if not conexao:
                            print(
                                f"[Channel Factory -> Evolution] Nenhuma conexão Evolution válida encontrada "
                                f"para empresa={empresa_id} conexao_id={conexao_id}."
                            )
                            sucesso_geral = False
                            continue

                        outbound_payload = StandardOutgoingMessage(
                            identificador_contato=str(identificador_origem or "").strip(),
                            canal="whatsapp",
                            texto=parte,
                            tipo="text",
                            media_url=None,
                        )
                        await dispatch_outbound_message(
                            empresa_id=conexao.empresa_id,
                            conexao=conexao,
                            payload=outbound_payload,
                        )
                except Exception as e:
                    print(f"[Channel Factory -> Evolution] Erro ao despachar via conexão {conexao_id}: {e}")
                    traceback.print_exc()
                    sucesso_geral = False
                
            case "chatwoot":
                print(f"[Channel Factory -> Chatwoot] Disparando para {identificador_origem}: {parte}")
                # Mock HTTP:
                # async with httpx.AsyncClient() as client:
                #     await client.post(f"mock_url_chatwoot/conversations/{identificador_origem}/messages", json={"content": parte})
                
            case "telegram":
                print(f"[Channel Factory -> Telegram] Disparando para {identificador_origem}: {parte}")
                
            case "simulador":
                print(f"[Channel Factory] Despachando via Conexão ID: {conexao_id} para o canal: {canal}")
                print(f"[Channel Factory -> Simulador] Gravando resposta no Redis para sessão: {identificador_origem}")
                from app.api.main import redis_client
                # No simulador, o identificador_origem é o sessao_id
                # Mas aqui concatenamos se houver múltiplas partes? 
                # O ideal é que o simulador leia mensagens uma a uma ou as concatene.
                # Como o polling do simulador deleta a chave, vamos usar um append ou timeout curto.
                # Para simplificar e manter compatibilidade com o polling atual que deleta:
                await redis_client.setex(f"sim_resp:{identificador_origem}", 300, parte)
                
            case _:
                print(f"[Channel Factory] Despachando via Conexão ID: {conexao_id} para o canal: {canal}")
                print(f"[Channel Factory -> {canal}] (Canal desconhecido) Disparando para {identificador_origem}: {parte}")

    return sucesso_geral

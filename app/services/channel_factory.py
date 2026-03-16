import asyncio
import uuid

from db.database import AsyncSessionLocal
from db.models import Conexao, TipoConexao
from sqlalchemy import select


async def despachar_mensagem(
    canal: str,
    identificador_origem: str,
    texto: str,
    conexao_id: str | None = None,
):
    """
    Função responsável por aplicar o delay 'humano' e enviar a mensagem para o canal final.
    """
    if not texto:
        return False

    # Lógica de Humanização: quebrando a resposta usando quebra de linha
    # Também poderia incluir divisão por '.' se o texto for muito longo e sem parágrafos
    partes = [p.strip() for p in texto.split('\n') if p.strip()]
    
    if not partes:
        partes = [texto]
        
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
                print(f"[Channel Factory] Despachando via Conexão ID: {conexao_id} para o canal: {canal}")

                if not conexao_id:
                    print(f"[Channel Factory -> Evolution] Fallback seguro: conexao_id ausente para {identificador_origem}.")
                    sucesso_geral = False
                    continue

                try:
                    from app.services.evolution_service import enviar_mensagem_whatsapp_por_credenciais

                    async with AsyncSessionLocal() as session:
                        result = await session.execute(
                            select(Conexao).where(Conexao.id == uuid.UUID(conexao_id))
                        )
                        conexao = result.scalars().first()

                        if not conexao:
                            print(f"[Channel Factory -> Evolution] Conexão {conexao_id} não encontrada.")
                            sucesso_geral = False
                            continue

                        if conexao.tipo != TipoConexao.EVOLUTION:
                            print(f"[Channel Factory -> Evolution] Conexão {conexao_id} não pertence ao canal evolution.")
                            sucesso_geral = False
                            continue

                        enviado = await enviar_mensagem_whatsapp_por_credenciais(
                            identificador_origem,
                            parte,
                            conexao.credenciais,
                        )
                        if not enviado:
                            sucesso_geral = False
                except Exception as e:
                    print(f"[Channel Factory -> Evolution] Erro ao despachar via conexão {conexao_id}: {e}")
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

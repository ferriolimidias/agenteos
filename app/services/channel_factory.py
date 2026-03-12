import asyncio
import httpx

async def despachar_mensagem(canal: str, identificador_origem: str, texto: str):
    """
    Função responsável por aplicar o delay 'humano' e enviar a mensagem para o canal final.
    """
    if not texto:
        return

    # Lógica de Humanização: quebrando a resposta usando quebra de linha
    # Também poderia incluir divisão por '.' se o texto for muito longo e sem parágrafos
    partes = [p.strip() for p in texto.split('\n') if p.strip()]
    
    if not partes:
        partes = [texto]
        
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
                print(f"[Channel Factory -> Evolution] Disparando para {identificador_origem}: {parte}")
                # Mock HTTP:
                # async with httpx.AsyncClient() as client:
                #     await client.post("mock_url_evolution", json={"number": identificador_origem, "text": parte})
                
            case "chatwoot":
                print(f"[Channel Factory -> Chatwoot] Disparando para {identificador_origem}: {parte}")
                # Mock HTTP:
                # async with httpx.AsyncClient() as client:
                #     await client.post(f"mock_url_chatwoot/conversations/{identificador_origem}/messages", json={"content": parte})
                
            case "telegram":
                print(f"[Channel Factory -> Telegram] Disparando para {identificador_origem}: {parte}")
                
            case "simulador":
                print(f"[Channel Factory -> Simulador] Gravando resposta no Redis para sessão: {identificador_origem}")
                from app.api.main import redis_client
                # No simulador, o identificador_origem é o sessao_id
                # Mas aqui concatenamos se houver múltiplas partes? 
                # O ideal é que o simulador leia mensagens uma a uma ou as concatene.
                # Como o polling do simulador deleta a chave, vamos usar um append ou timeout curto.
                # Para simplificar e manter compatibilidade com o polling atual que deleta:
                await redis_client.setex(f"sim_resp:{identificador_origem}", 300, parte)
                
            case _:
                print(f"[Channel Factory -> {canal}] (Canal desconhecido) Disparando para {identificador_origem}: {parte}")

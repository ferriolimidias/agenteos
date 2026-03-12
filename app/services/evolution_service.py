import httpx
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Empresa

async def enviar_mensagem_whatsapp(empresa_id: UUID, telefone: str, texto: str, db: AsyncSession):
    """
    Envia uma mensagem de texto via instãncia Evolution API do cliente.
    """
    try:
        # Busca a empresa para pegar as credenciais
        result = await db.execute(select(Empresa).where(Empresa.id == empresa_id))
        empresa = result.scalars().first()
        
        if not empresa or not empresa.credenciais_canais:
            print(f"[Evolution Service] Credenciais não configuradas para a empresa {empresa_id}")
            return False
            
        credenciais = empresa.credenciais_canais
        evolution_url = credenciais.get("evolution_url")
        evolution_apikey = credenciais.get("evolution_apikey")
        evolution_instance = credenciais.get("evolution_instance")
        
        if not all([evolution_url, evolution_apikey, evolution_instance]):
            print(f"[Evolution Service] Configuração incompleta para a empresa {empresa_id} na Evolution.")
            return False
            
        # Formata a URL (removendo a barra no final, se houver)
        base_url = evolution_url.rstrip('/')
        endpoint = f"{base_url}/message/sendText/{evolution_instance}"
        
        headers = {
            "apikey": evolution_apikey,
            "Content-Type": "application/json"
        }
        
        payload = {
            "number": telefone,
            "text": texto
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(endpoint, headers=headers, json=payload, timeout=10.0)
            
            if response.status_code in (200, 201):
                print(f"[Evolution Service] 🚀 Mensagem enviada com sucesso para {telefone}")
                return True
            else:
                print(f"[Evolution Service] ⚠️ Erro ao enviar. Status: {response.status_code}. Detalhe: {response.text}")
                return False
                
    except Exception as e:
        print(f"[Evolution Service] 🔥 Falha severa ao enviar mensagem para {telefone}: {str(e)}")
        return False

async def get_base64_media(empresa_id: UUID, message: dict, db: AsyncSession) -> str | None:
    """
    Recupera a mídia em base64 através do endpoint da Evolution API.
    """
    try:
        result = await db.execute(select(Empresa).where(Empresa.id == empresa_id))
        empresa = result.scalars().first()
        
        if not empresa or not empresa.credenciais_canais:
            print(f"[Evolution Service] Credenciais não configuradas para a empresa {empresa_id}")
            return None
            
        credenciais = empresa.credenciais_canais
        evolution_url = credenciais.get("evolution_url")
        evolution_apikey = credenciais.get("evolution_apikey")
        evolution_instance = credenciais.get("evolution_instance")
        
        if not all([evolution_url, evolution_apikey, evolution_instance]):
            print(f"[Evolution Service] Configuração incompleta para baixar mídia da empresa {empresa_id}")
            return None
            
        base_url = evolution_url.rstrip('/')
        endpoint = f"{base_url}/chat/getBase64FromMediaMessage/{evolution_instance}"
        
        headers = {
            "apikey": evolution_apikey,
            "Content-Type": "application/json"
        }
        
        payload = {
            "message": message
        }
        
        async with httpx.AsyncClient() as client:
            response = await client.post(endpoint, headers=headers, json=payload, timeout=30.0)
            
            if response.status_code in (200, 201):
                data = response.json()
                return data.get("base64")
            else:
                print(f"[Evolution Service] ⚠️ Erro ao baixar mídia. Status: {response.status_code}. Detalhe: {response.text}")
                return None
                
    except Exception as e:
        print(f"[Evolution Service] 🔥 Erro ao obter base64 da mídia: {str(e)}")
        return None

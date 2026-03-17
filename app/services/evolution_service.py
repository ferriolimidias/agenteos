import httpx
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Empresa, Conexao
from db.database import AsyncSessionLocal


async def enviar_mensagem_whatsapp_por_credenciais(
    telefone: str,
    texto: str,
    credenciais: dict | None,
) -> bool:
    """
    Envia uma mensagem de texto usando credenciais ja resolvidas da Evolution.
    """
    try:
        credenciais = credenciais or {}
        evolution_url = credenciais.get("evolution_url")
        evolution_apikey = credenciais.get("evolution_apikey")
        evolution_instance = credenciais.get("evolution_instance")

        if not all([evolution_url, evolution_apikey, evolution_instance]):
            print("[Evolution Service] Configuração incompleta para envio por conexao.")
            return False

        base_url = evolution_url.rstrip("/")
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
                print(f"[Evolution Service] Mensagem enviada com sucesso para {telefone}")
                return True

            print(f"[Evolution Service] Erro ao enviar. Status: {response.status_code}.")
            return False

    except Exception as e:
        print(f"[Evolution Service] Falha severa ao enviar mensagem para {telefone}: {str(e)}")
        return False

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
        return await enviar_mensagem_whatsapp_por_credenciais(telefone, texto, credenciais)
                
    except Exception as e:
        print(f"[Evolution Service] 🔥 Falha severa ao enviar mensagem para {telefone}: {str(e)}")
        return False

async def _obter_credenciais_evolution(
    empresa_id: UUID,
    db: AsyncSession,
    conexao_id: str | UUID | None = None,
) -> dict | None:
    if conexao_id:
        try:
            conexao_uuid = UUID(str(conexao_id))
            result_conexao = await db.execute(select(Conexao).where(Conexao.id == conexao_uuid))
            conexao = result_conexao.scalars().first()
            if conexao and conexao.credenciais:
                return conexao.credenciais
        except Exception as e:
            print(f"[Evolution Service] Aviso ao carregar conexão {conexao_id}: {e}")

    result = await db.execute(select(Empresa).where(Empresa.id == empresa_id))
    empresa = result.scalars().first()

    if not empresa or not empresa.credenciais_canais:
        print(f"[Evolution Service] Credenciais não configuradas para a empresa {empresa_id}")
        return None

    return empresa.credenciais_canais


def _extrair_base64_qrcode(payload: dict | None) -> str | None:
    if not isinstance(payload, dict):
        return None

    candidatos = [
        payload.get("base64"),
        payload.get("qrcode"),
        payload.get("qr"),
        payload.get("qrCode"),
        payload.get("code"),
    ]

    instance_data = payload.get("instance")
    if isinstance(instance_data, dict):
        candidatos.extend(
            [
                instance_data.get("base64"),
                instance_data.get("qrcode"),
                instance_data.get("qr"),
                instance_data.get("qrCode"),
                instance_data.get("code"),
            ]
        )

    for candidato in candidatos:
        if isinstance(candidato, str) and candidato.strip():
            valor = candidato.strip()
            if "," in valor and valor.startswith("data:image"):
                return valor.split(",", 1)[1]
            return valor

    return None


async def obter_qr_code(conexao_id: str | UUID) -> dict:
    try:
        conexao_uuid = UUID(str(conexao_id))
    except (ValueError, TypeError):
        return {"success": False, "detail": "Conexão inválida."}

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Conexao).where(Conexao.id == conexao_uuid))
        conexao = result.scalars().first()

        if not conexao:
            return {"success": False, "detail": "Conexão não encontrada."}

        credenciais = conexao.credenciais or {}
        evolution_url = credenciais.get("evolution_url")
        evolution_apikey = credenciais.get("evolution_apikey")
        evolution_instance = credenciais.get("evolution_instance")

        if not all([evolution_url, evolution_apikey, evolution_instance]):
            return {
                "success": False,
                "detail": "Configuração incompleta da conexão Evolution para gerar QR Code.",
            }

        headers = {"apikey": evolution_apikey}
        base_url = str(evolution_url).rstrip("/")
        endpoint_status = f"{base_url}/instance/connectionState/{evolution_instance}"
        endpoint_connect = f"{base_url}/instance/connect/{evolution_instance}"

        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=8.0)) as client:
            try:
                response_status = await client.get(endpoint_status, headers=headers)
                data_status = response_status.json() if response_status.content else {}
                current_state = (
                    data_status.get("instance", {}).get("state")
                    or data_status.get("state")
                    or data_status.get("status")
                    or data_status.get("connectionStatus")
                    or ""
                )
                if str(current_state).upper() == "CONNECTED":
                    return {
                        "success": False,
                        "already_connected": True,
                        "detail": "Esta instância já está conectada.",
                    }
            except Exception:
                pass

            try:
                response = await client.get(endpoint_connect, headers=headers)
            except Exception as exc:
                return {
                    "success": False,
                    "detail": f"Falha ao solicitar QR Code na Evolution: {exc}",
                }

        if response.status_code >= 400:
            detalhe = response.text[:500] if response.text else f"status {response.status_code}"
            if "connected" in detalhe.lower():
                return {
                    "success": False,
                    "already_connected": True,
                    "detail": "Esta instância já está conectada.",
                }
            return {
                "success": False,
                "detail": f"Erro ao obter QR Code na Evolution: {detalhe}",
            }

        try:
            payload = response.json() if response.content else {}
        except Exception:
            payload = {}

        qr_code_base64 = _extrair_base64_qrcode(payload)
        if not qr_code_base64:
            return {
                "success": False,
                "detail": "A Evolution não retornou um QR Code válido para esta instância.",
            }

        return {
            "success": True,
            "base64": qr_code_base64,
            "detail": "QR Code gerado com sucesso.",
        }


async def get_base64_media(
    empresa_id: UUID,
    message: dict,
    db: AsyncSession,
    conexao_id: str | UUID | None = None,
) -> str | None:
    """
    Recupera a mídia em base64 através do endpoint da Evolution API.
    """
    try:
        credenciais = await _obter_credenciais_evolution(empresa_id, db, conexao_id=conexao_id)
        if not credenciais:
            return None

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
                print(f"[Evolution Service] Erro ao baixar mídia. Status: {response.status_code}.")
                return None
                
    except Exception as e:
        print(f"[Evolution Service] 🔥 Erro ao obter base64 da mídia: {str(e)}")
        return None

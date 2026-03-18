import httpx
import re
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Empresa, Conexao
from db.database import AsyncSessionLocal


def _mask_apikey(value: str | None) -> str:
    key = str(value or "").strip()
    if not key:
        return ""
    if len(key) <= 6:
        return "*" * len(key)
    return f"{key[:3]}***{key[-3:]}"


def _normalizar_status_evolution(raw_status: str | None) -> str:
    status = str(raw_status or "").strip().lower()
    if status in {"open", "connected", "online"}:
        return "open"
    if status in {"connecting", "qrcode", "qr", "pairing"}:
        return "connecting"
    return "disconnected"


def _normalizar_numero_destino(telefone: str | None) -> str:
    valor = str(telefone or "").strip()
    if not valor:
        return ""
    if "@s.whatsapp.net" in valor:
        valor = valor.replace("@s.whatsapp.net", "")
    return re.sub(r"\D", "", valor)


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

        numero_normalizado = _normalizar_numero_destino(telefone)
        payload = {
            "number": numero_normalizado or str(telefone or "").strip(),
            "text": texto
        }

        json_payload = payload
        url = endpoint
        print(f"DEBUG ENVIO: URL -> {url}")
        print(f"DEBUG ENVIO: Payload -> {json_payload}")

        async with httpx.AsyncClient() as client:
            response = await client.post(endpoint, headers=headers, json=payload, timeout=10.0)
            print(f"DEBUG ENVIO: Status Code -> {response.status_code}")
            print(f"DEBUG ENVIO: Response -> {response.text}")

            if response.status_code in (200, 201):
                print(f"[Evolution Service] Mensagem enviada com sucesso para {numero_normalizado or telefone}")
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


async def enviar_midia_base64(
    conexao: Conexao,
    numero: str,
    base64_data: str,
    tipo: str,
    mimetype: str,
    caption: str | None = None,
) -> bool:
    """
    Envia mídia em base64 via Evolution API.
    Tipos suportados: image, audio, document.
    """
    try:
        credenciais = conexao.credenciais or {}
        evolution_url = credenciais.get("evolution_url")
        evolution_apikey = credenciais.get("evolution_apikey")
        evolution_instance = credenciais.get("evolution_instance")

        if not all([evolution_url, evolution_apikey, evolution_instance]):
            print("[Evolution Service] Configuração incompleta para envio de mídia.")
            return False

        base_url = str(evolution_url).rstrip("/")
        endpoint = f"{base_url}/message/sendMedia/{evolution_instance}"
        headers = {
            "apikey": evolution_apikey,
            "Content-Type": "application/json",
        }

        payload = {
            "number": str(numero or "").strip(),
            "mediatype": str(tipo or "document").strip().lower(),
            "mimetype": mimetype or "application/octet-stream",
            "media": base64_data,
            "caption": caption or "",
        }

        async with httpx.AsyncClient() as client:
            response = await client.post(endpoint, headers=headers, json=payload, timeout=30.0)

        if response.status_code in (200, 201):
            return True

        print(f"[Evolution Service] Erro ao enviar mídia. Status={response.status_code} Body={response.text[:250]}")
        return False
    except Exception as e:
        print(f"[Evolution Service] Falha ao enviar mídia: {str(e)}")
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


async def _obter_credenciais_conexao_ou_empresa(session: AsyncSession, conexao: Conexao) -> dict | None:
    credenciais_conexao = dict(conexao.credenciais or {})
    evolution_url = str(credenciais_conexao.get("evolution_url") or "").strip()
    evolution_apikey = str(credenciais_conexao.get("evolution_apikey") or "").strip()
    evolution_instance = str(credenciais_conexao.get("evolution_instance") or "").strip()

    if all([evolution_url, evolution_apikey, evolution_instance]):
        return {
            "evolution_url": evolution_url,
            "evolution_apikey": evolution_apikey,
            "evolution_instance": evolution_instance,
        }

    result_empresa = await session.execute(select(Empresa).where(Empresa.id == conexao.empresa_id))
    empresa = result_empresa.scalars().first()
    credenciais_empresa = dict((empresa.credenciais_canais or {}) if empresa else {})

    evolution_url = evolution_url or str(credenciais_empresa.get("evolution_url") or "").strip()
    evolution_apikey = evolution_apikey or str(credenciais_empresa.get("evolution_apikey") or "").strip()
    evolution_instance = evolution_instance or str(credenciais_empresa.get("evolution_instance") or "").strip()

    if not all([evolution_url, evolution_apikey, evolution_instance]):
        return None

    return {
        "evolution_url": evolution_url,
        "evolution_apikey": evolution_apikey,
        "evolution_instance": evolution_instance,
    }


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

        credenciais = await _obter_credenciais_conexao_ou_empresa(session, conexao)
        if not credenciais:
            return {
                "success": False,
                "detail": "Configuração incompleta da conexão Evolution para gerar QR Code.",
            }
        evolution_url = credenciais["evolution_url"]
        evolution_apikey = credenciais["evolution_apikey"]
        evolution_instance = credenciais["evolution_instance"]

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
            if response.status_code in (400, 401):
                print(
                    f"[Evolution Service] Falha auth/req QRCode | url={endpoint_connect} | "
                    f"headers={{'apikey': '{_mask_apikey(evolution_apikey)}'}} | status={response.status_code}"
                )
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


async def consultar_status_instancia(
    instance_name: str,
    evolution_url: str,
    evolution_apikey: str,
) -> dict:
    base_url = str(evolution_url or "").rstrip("/")
    instance = str(instance_name or "").strip()
    apikey = str(evolution_apikey or "").strip()

    if not base_url:
        return {"success": False, "detail": "Evolution URL não configurada."}
    if not instance:
        return {"success": False, "detail": "Nome da instância não informado."}
    if not apikey:
        return {"success": False, "detail": "API Key da Evolution não configurada."}

    endpoint_status = f"{base_url}/instance/connectionState/{instance}"
    headers = {"apikey": apikey}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(15.0, connect=8.0)) as client:
            response = await client.get(endpoint_status, headers=headers)
    except Exception as exc:
        return {"success": False, "detail": f"Falha ao consultar status da instância: {exc}"}

    if response.status_code >= 400:
        if response.status_code in (400, 401):
            print(
                f"[Evolution Service] Falha auth/req Status | url={endpoint_status} | "
                f"headers={{'apikey': '{_mask_apikey(apikey)}'}} | status={response.status_code}"
            )
        detalhe = response.text[:300] if response.text else f"status {response.status_code}"
        return {"success": False, "detail": f"Erro ao consultar status da instância: {detalhe}"}

    try:
        data = response.json() if response.content else {}
    except Exception:
        data = {}

    raw_status = (
        data.get("instance", {}).get("state")
        or data.get("state")
        or data.get("status")
        or data.get("connectionStatus")
        or ""
    )
    status_normalizado = _normalizar_status_evolution(raw_status)
    return {
        "success": True,
        "status": status_normalizado,
        "raw_status": raw_status,
    }


async def consultar_status_conexao(conexao_id: str | UUID) -> dict:
    try:
        conexao_uuid = UUID(str(conexao_id))
    except (ValueError, TypeError):
        return {"success": False, "detail": "Conexão inválida."}

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Conexao).where(Conexao.id == conexao_uuid))
        conexao = result.scalars().first()
        if not conexao:
            return {"success": False, "detail": "Conexão não encontrada."}

        credenciais = await _obter_credenciais_conexao_ou_empresa(session, conexao)
        if not credenciais:
            return {"success": False, "detail": "Configuração incompleta da conexão Evolution."}

        resultado = await consultar_status_instancia(
            instance_name=credenciais["evolution_instance"],
            evolution_url=credenciais["evolution_url"],
            evolution_apikey=credenciais["evolution_apikey"],
        )
        if not resultado.get("success"):
            return resultado

        return {
            "success": True,
            "status": resultado.get("status", "disconnected"),
            "raw_status": resultado.get("raw_status"),
            "conexao_id": str(conexao.id),
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

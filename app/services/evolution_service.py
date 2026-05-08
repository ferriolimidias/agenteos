import os
import httpx
import re
from uuid import UUID
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Empresa, Conexao, TipoConexao
from db.database import AsyncSessionLocal


def evolution_base_url() -> str:
    return (os.getenv("EVOLUTION_API_URL") or "").strip().rstrip("/")


def evolution_global_api_key() -> str:
    return (os.getenv("EVOLUTION_API_TOKEN") or "").strip()


def webhook_base_url() -> str:
    """
    URL pública base do sistema para callbacks externos.
    Prioriza URL_BASE_DO_SISTEMA e aceita aliases comuns.
    """
    return (
        os.getenv("URL_BASE_DO_SISTEMA")
        or os.getenv("PUBLIC_BASE_URL")
        or os.getenv("APP_BASE_URL")
        or ""
    ).strip().rstrip("/")


def _headers_evolution(apikey: str) -> dict:
    return {
        "apikey": str(apikey or "").strip(),
        "Content-Type": "application/json",
    }


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
        evolution_url = credenciais.get("evolution_url") or evolution_base_url()
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
        evolution_url = credenciais.get("evolution_url") or evolution_base_url()
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

def _credenciais_com_base_url(creds: dict | None) -> dict | None:
    if not creds:
        return None
    out = dict(creds)
    base = evolution_base_url()
    out["evolution_url"] = str(out.get("evolution_url") or "").strip() or base
    return out


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
                return _credenciais_com_base_url(conexao.credenciais)
        except Exception as e:
            print(f"[Evolution Service] Aviso ao carregar conexão {conexao_id}: {e}")

    result = await db.execute(select(Empresa).where(Empresa.id == empresa_id))
    empresa = result.scalars().first()

    if not empresa or not empresa.credenciais_canais:
        print(f"[Evolution Service] Credenciais não configuradas para a empresa {empresa_id}")
        return None

    return _credenciais_com_base_url(empresa.credenciais_canais)


async def _obter_credenciais_conexao_ou_empresa(session: AsyncSession, conexao: Conexao) -> dict | None:
    credenciais_conexao = dict(conexao.credenciais or {})
    base = evolution_base_url()
    evolution_url = str(credenciais_conexao.get("evolution_url") or "").strip() or base
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

    evolution_apikey = evolution_apikey or str(credenciais_empresa.get("evolution_apikey") or "").strip()
    evolution_instance = evolution_instance or str(credenciais_empresa.get("evolution_instance") or "").strip()
    evolution_url = evolution_url or base

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

        global_key = evolution_global_api_key()
        headers = {"apikey": global_key or evolution_apikey}
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
    global_key = evolution_global_api_key()

    if not base_url:
        return {"success": False, "detail": "Evolution URL não configurada."}
    if not instance:
        return {"success": False, "detail": "Nome da instância não informado."}
    if not apikey:
        return {"success": False, "detail": "API Key da Evolution não configurada."}

    endpoint_status = f"{base_url}/instance/connectionState/{instance}"
    headers = {"apikey": global_key or apikey}

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

        evolution_url = credenciais.get("evolution_url") or evolution_base_url()
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


def _nome_instancia_padrao_empresa(empresa_uuid: UUID) -> str:
    """
    Nome canónico único da instância Evolution criada pelo provisionamento automático (1:1 com a empresa).
    Deriva-se só do UUID interno; não aceite nomes vindos de input HTTP para operações de delete/reset.
    """
    suffix = str(empresa_uuid).replace("-", "").lower()
    nome = f"wa_{suffix}"
    if not re.fullmatch(r"^wa_[0-9a-f]{32}$", nome):
        raise ValueError("Invariante de tenant: UUID de empresa inválido para nome de instância.")
    return nome


def _evolution_create_indicates_existing_instance(resp: httpx.Response) -> bool:
    if resp.status_code not in (400, 403, 409, 422):
        return False
    text = (resp.text or "").lower()
    if "already" in text and ("use" in text or "exists" in text or "in use" in text):
        return True
    if "in use" in text:
        return True
    if "duplicate" in text or "em uso" in text or "já existe" in text:
        return True
    return False


async def _buscar_apikey_instancia_evolution(
    base: str,
    gkey: str,
    instance_name: str,
) -> str | None:
    """Tenta obter a apikey da instância via fetchInstances filtrado pelo nome (sem listar todas as instâncias)."""
    root = base.rstrip("/")
    url = f"{root}/instance/fetchInstances"
    headers = {"apikey": gkey}
    timeout = httpx.Timeout(20.0, connect=8.0)
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            r = await client.get(url, headers=headers, params={"instanceName": instance_name})
            if r.status_code >= 400:
                return None
            data = r.json() if r.content else {}
    except Exception as exc:
        print(f"[Evolution Service] fetchInstances falhou: {exc}")
        return None

    candidatos: list = []
    if isinstance(data, list):
        candidatos = data
    elif isinstance(data, dict):
        for k in ("instances", "instance", "data", "response"):
            v = data.get(k)
            if isinstance(v, list):
                candidatos = v
                break
            if isinstance(v, dict):
                candidatos = [v]
                break
        if not candidatos and data.get("name") == instance_name:
            candidatos = [data]

    nome_alvo = str(instance_name or "").strip().lower()
    for item in candidatos:
        if not isinstance(item, dict):
            continue
        nome = str(item.get("name") or item.get("instanceName") or item.get("instance") or "").strip().lower()
        if nome and nome != nome_alvo:
            continue
        for k in ("apikey", "apiKey", "token", "hash"):
            v = item.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
            if isinstance(v, dict):
                for sk in ("apikey", "apiKey", "token"):
                    sv = v.get(sk)
                    if isinstance(sv, str) and sv.strip():
                        return sv.strip()
    return None


async def _solicitar_qrcode_connect(
    base: str,
    instance_name: str,
    apikey_primary: str,
    apikey_fallback: str | None = None,
) -> tuple[str | None, str]:
    root = base.rstrip("/")
    ep = f"{root}/instance/connect/{instance_name}"
    timeout = httpx.Timeout(30.0, connect=10.0)
    for key in filter(None, [apikey_primary, apikey_fallback]):
        key = str(key).strip()
        if not key:
            continue
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                r = await client.get(ep, headers={"apikey": key})
            if r.status_code >= 400:
                continue
            payload = r.json() if r.content else {}
            qr = _extrair_base64_qrcode(payload if isinstance(payload, dict) else {})
            if qr:
                return qr, key
        except Exception as exc:
            print(f"[Evolution Service] connect {instance_name} com chave alternativa: {exc}")
            continue
    return None, apikey_primary


def _montar_url_webhook_empresa(empresa_id: str) -> str:
    base = webhook_base_url() or "http://backend:8000"
    return f"{base.rstrip('/')}/api/webhook/{empresa_id}/evolution"


async def _configurar_webhook_instancia(base: str, instance_name: str, apikey: str, empresa_id: str) -> bool:
    wh_url = _montar_url_webhook_empresa(empresa_id)
    payload = {
        "enabled": True,
        "url": wh_url,
        "byEvents": False,
        "webhook_by_events": False,
        "events": ["MESSAGES_UPSERT", "CONNECTION_UPDATE"],
    }
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(12.0, connect=5.0)) as client:
            resp = await client.post(
                f"{base.rstrip('/')}/webhook/set/{instance_name}",
                headers=_headers_evolution(apikey),
                json=payload,
            )
        if resp.status_code >= 400:
            print(
                f"[Evolution Service] Falha ao configurar webhook da instância {instance_name} "
                f"(status={resp.status_code}, body={resp.text[:300]})"
            )
            return False
        return True
    except Exception as exc:
        print(f"[Evolution Service] Falha ao configurar webhook por instância ({exc}).")
        return False


def _extrair_token_instancia_criada(payload: dict | None) -> str | None:
    if not isinstance(payload, dict):
        return None
    h = payload.get("hash")
    if isinstance(h, str) and h.strip():
        return h.strip()
    if isinstance(h, dict):
        for k in ("apikey", "token", "apiKey"):
            v = h.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    inst = payload.get("instance")
    if isinstance(inst, dict):
        for k in ("apikey", "token", "apiKey"):
            v = inst.get(k)
            if isinstance(v, str) and v.strip():
                return v.strip()
    for k in ("apikey", "token"):
        v = payload.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


async def provisionar_whatsapp_empresa(empresa_id: str, db: AsyncSession) -> dict:
    """
    Cria instância na Evolution (URL/token globais via ambiente), persiste Conexão e devolve QR Base64.
    Se já existir conexão Evolution para a empresa, apenas solicita novo QR na instância existente.
    """
    base = evolution_base_url()
    gkey = evolution_global_api_key()
    if not base or not gkey:
        return {"success": False, "detail": "Defina EVOLUTION_API_URL e EVOLUTION_API_TOKEN no servidor."}

    try:
        empresa_uuid = UUID(str(empresa_id))
    except ValueError:
        return {"success": False, "detail": "Identificador de empresa inválido."}

    res_prev = await db.execute(
        select(Conexao)
        .where(
            Conexao.empresa_id == empresa_uuid,
            Conexao.tipo == TipoConexao.EVOLUTION,
        )
        .order_by(Conexao.criado_em.desc())
    )
    existente = res_prev.scalars().first()
    if existente:
        q = await obter_qr_code(existente.id)
        if q.get("success"):
            return {
                "success": True,
                "conexao_id": str(existente.id),
                "instance_name": existente.nome_instancia,
                "base64": q.get("base64"),
                "detail": q.get("detail") or "QR Code gerado.",
                "reaproveitada": True,
            }
        return {
            "success": False,
            "detail": q.get("detail") or "Não foi possível obter o QR Code.",
            "conexao_id": str(existente.id),
        }

    try:
        instance_name = _nome_instancia_padrao_empresa(empresa_uuid)
    except ValueError as exc:
        return {"success": False, "detail": str(exc)}
    headers = _headers_evolution(gkey)
    body = {
        "instanceName": instance_name,
        "integration": "WHATSAPP-BAILEYS",
        "qrcode": True,
    }
    url_create = f"{base}/instance/create"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=15.0)) as client:
            resp = await client.post(url_create, headers=headers, json=body)
    except Exception as exc:
        return {"success": False, "detail": f"Falha ao criar instância na Evolution: {exc}"}

    if resp.status_code not in (200, 201):
        if not _evolution_create_indicates_existing_instance(resp):
            texto = (resp.text or "")[:500]
            return {"success": False, "detail": texto or f"Evolution retornou status {resp.status_code}."}

        inst_key = await _buscar_apikey_instancia_evolution(base, gkey, instance_name)
        token_inst = (inst_key or gkey).strip()
        qr_b64, key_para_usar = await _solicitar_qrcode_connect(
            base,
            instance_name,
            token_inst,
            gkey if token_inst != gkey else None,
        )
        if not qr_b64:
            return {
                "success": False,
                "detail": (
                    "A instância WhatsApp desta empresa já existe na Evolution, mas não foi possível obter o QR Code. "
                    "Utilize «Forçar reinício» no modal para apagar a instância e voltar a tentar."
                ),
            }

        webhook_ok = await _configurar_webhook_instancia(base, instance_name, key_para_usar, empresa_id)
        if not webhook_ok:
            return {
                "success": False,
                "detail": (
                    "A instância foi encontrada, mas não foi possível configurar o webhook obrigatório da Evolution. "
                    "Verifique URL_BASE_DO_SISTEMA no servidor e tente novamente."
                ),
            }

        conexao = Conexao(
            empresa_id=empresa_uuid,
            tipo=TipoConexao.EVOLUTION,
            nome_instancia=instance_name,
            credenciais={
                "evolution_instance": instance_name,
                "evolution_apikey": key_para_usar,
            },
            status="ativo",
        )
        db.add(conexao)
        try:
            await db.commit()
            await db.refresh(conexao)
        except Exception as exc:
            await db.rollback()
            return {"success": False, "detail": f"Erro ao salvar conexão: {exc}"}

        return {
            "success": True,
            "conexao_id": str(conexao.id),
            "instance_name": instance_name,
            "base64": qr_b64,
            "detail": "Instância já existia na Evolution; ligámo-nos a ela e geramos um novo QR Code.",
            "reaproveitada": True,
        }

    try:
        data = resp.json() if resp.content else {}
    except Exception:
        data = {}

    token_inst = _extrair_token_instancia_criada(data)
    if not token_inst:
        return {"success": False, "detail": "A Evolution não retornou o token/apikey da nova instância."}

    qr_b64 = _extrair_base64_qrcode(data)
    if not qr_b64:
        ep = f"{base}/instance/connect/{instance_name}"
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as client:
                r2 = await client.get(ep, headers={"apikey": gkey or token_inst})
            if r2.status_code in (200, 201) and r2.content:
                try:
                    payload2 = r2.json()
                except Exception:
                    payload2 = {}
                qr_b64 = _extrair_base64_qrcode(payload2)
        except Exception as exc:
            print(f"[Evolution Service] Aviso ao solicitar QR após create: {exc}")

    webhook_ok = await _configurar_webhook_instancia(base, instance_name, gkey or token_inst, empresa_id)
    if not webhook_ok:
        return {
            "success": False,
            "detail": (
                "Instância criada, mas falhou a configuração do webhook obrigatório da Evolution. "
                "Verifique URL_BASE_DO_SISTEMA no servidor e tente novamente."
            ),
        }

    conexao = Conexao(
        empresa_id=empresa_uuid,
        tipo=TipoConexao.EVOLUTION,
        nome_instancia=instance_name,
        credenciais={
            "evolution_instance": instance_name,
            "evolution_apikey": token_inst,
        },
        status="ativo",
    )
    db.add(conexao)
    try:
        await db.commit()
        await db.refresh(conexao)
    except Exception as exc:
        await db.rollback()
        return {"success": False, "detail": f"Erro ao salvar conexão: {exc}"}

    if not qr_b64:
        return {
            "success": False,
            "detail": "Instância criada, mas a Evolution não retornou QR Code. Tente novamente em instantes.",
            "conexao_id": str(conexao.id),
            "instance_name": instance_name,
        }

    return {
        "success": True,
        "conexao_id": str(conexao.id),
        "instance_name": instance_name,
        "base64": qr_b64,
        "detail": "Instância criada. Escaneie o QR Code para conectar.",
        "reaproveitada": False,
    }


async def reset_evolution_whatsapp_empresa(empresa_id: str, db: AsyncSession) -> dict:
    """
    Remove apenas a instância Evolution automática deste tenant (wa_<uuid da empresa>).

    Isolamento: o segmento do DELETE é derivado exclusivamente de empresa_id autenticado na rota;
    não há listagem nem remoção em massa de instâncias na Evolution.
    """
    base = evolution_base_url()
    gkey = evolution_global_api_key()
    if not base or not gkey:
        return {"success": False, "detail": "Defina EVOLUTION_API_URL e EVOLUTION_API_TOKEN no servidor."}

    try:
        empresa_uuid = UUID(str(empresa_id))
    except ValueError:
        return {"success": False, "detail": "Identificador de empresa inválido."}

    try:
        instance_name = _nome_instancia_padrao_empresa(empresa_uuid)
    except ValueError as exc:
        return {"success": False, "detail": str(exc)}

    root = base.rstrip("/")
    # Um único recurso: path fixo com nome canónico do tenant (sem parâmetros de bulk).
    url_del = f"{root}/instance/delete/{instance_name}"

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(45.0, connect=15.0), follow_redirects=True) as client:
            resp = await client.delete(url_del, headers={"apikey": gkey})
    except Exception as exc:
        return {"success": False, "detail": f"Falha ao contactar a Evolution: {exc}"}

    if resp.status_code >= 400 and resp.status_code != 404:
        texto = (resp.text or "")[:400]
        return {
            "success": False,
            "detail": texto or f"A Evolution recusou apagar a instância (HTTP {resp.status_code}).",
        }

    res_con = await db.execute(
        select(Conexao).where(
            Conexao.empresa_id == empresa_uuid,
            Conexao.tipo == TipoConexao.EVOLUTION,
        )
    )
    apagadas = 0
    for conexao in res_con.scalars().all():
        cred = conexao.credenciais or {}
        inst_cred = str(cred.get("evolution_instance") or "").strip()
        # Só remove linhas desta empresa que apontam explicitamente para a instância canónica deste tenant.
        if conexao.nome_instancia == instance_name or inst_cred == instance_name:
            await db.delete(conexao)
            apagadas += 1

    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        return {"success": False, "detail": f"Instância apagada na Evolution, mas erro ao atualizar a BD: {exc}"}

    return {
        "success": True,
        "detail": f"Instância {instance_name} removida. Pode voltar a conectar o WhatsApp.",
        "instance_name": instance_name,
        "conexoes_removidas": apagadas,
    }

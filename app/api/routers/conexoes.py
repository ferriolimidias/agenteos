import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.auth import get_current_user
from app.api.routers.empresas import require_ia_config_access
from app.services.evolution_service import (
    consultar_status_conexao,
    evolution_base_url,
    logout_evolution_whatsapp_empresa,
    obter_qr_code,
    provisionar_whatsapp_empresa,
    reset_evolution_whatsapp_empresa,
)
from app.schemas import ConexaoCreate, ConexaoResponse, ConexaoUpdate
from db.database import get_db
from db.models import Conexao, Empresa, TipoConexao, Usuario, is_admin_empresa_role, is_root_admin_email, is_super_admin_role


router = APIRouter(
    prefix="/empresas/{empresa_id}/conexoes",
    tags=["Conexoes"]
)

status_router = APIRouter(
    prefix="/conexoes",
    tags=["Conexoes"]
)

STATUSS_CONEXAO_ATIVOS = {"ativo", "connected", "open", "online"}


def _normalizar_tipo(tipo: str) -> TipoConexao:
    try:
        return TipoConexao(tipo)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Tipo de conexão inválido.") from exc


def _webhook_suffix(tipo: TipoConexao) -> str:
    if tipo == TipoConexao.EVOLUTION:
        return "evolution"
    if tipo in (TipoConexao.META, TipoConexao.INSTAGRAM):
        return "meta"
    return tipo.value


def _montar_webhook_url(request: Request, empresa_id: str, tipo: TipoConexao) -> tuple[str, str]:
    suffix = _webhook_suffix(tipo)
    base_url = str(request.base_url).rstrip("/")
    path = f"/api/webhook/{empresa_id}/{suffix}"
    return f"{base_url}{path}", path


def _mask_secret(value: str | None) -> str:
    if not value:
        return ""
    visible = value[-4:]
    return f"{'*' * max(len(value) - 4, 8)}{visible}"


def _mask_credenciais(tipo: TipoConexao, credenciais: dict[str, Any] | None) -> dict[str, Any]:
    credenciais = dict(credenciais or {})

    if tipo == TipoConexao.EVOLUTION and credenciais.get("evolution_apikey"):
        credenciais["evolution_apikey"] = _mask_secret(str(credenciais["evolution_apikey"]))

    if tipo in (TipoConexao.META, TipoConexao.INSTAGRAM) and credenciais.get("access_token"):
        credenciais["access_token"] = _mask_secret(str(credenciais["access_token"]))

    return credenciais


def _normalizar_status_conexao(status: str | None, default: str = "ativo") -> str:
    valor = str(status or "").strip()
    if not valor:
        return default
    if valor.lower() in STATUSS_CONEXAO_ATIVOS:
        return "ativo"
    return valor


def _validar_payload(tipo: TipoConexao, payload: ConexaoCreate) -> tuple[str, dict[str, Any]]:
    credenciais = payload.credenciais or {}

    if tipo == TipoConexao.EVOLUTION:
        base_url = evolution_base_url()
        if not str(credenciais.get("evolution_url") or "").strip():
            credenciais["evolution_url"] = base_url
        required_fields = ["evolution_apikey", "evolution_instance"]
        missing = [field for field in required_fields if not credenciais.get(field)]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Campos obrigatórios ausentes para Evolution: {', '.join(missing)}"
            )
        if not str(credenciais.get("evolution_url") or "").strip():
            raise HTTPException(
                status_code=503,
                detail="EVOLUTION_API_URL não configurada no servidor.",
            )
        evolution_instance = str(credenciais.get("evolution_instance") or "").strip()
        credenciais["evolution_instance"] = evolution_instance
        # "Nome amigável" opcional: quando vazio, usa automaticamente o identificador da instância.
        nome_instancia = (payload.nome_instancia or evolution_instance or "").strip()
        if not nome_instancia:
            raise HTTPException(status_code=400, detail="Nome da instância é obrigatório para Evolution.")
        return nome_instancia, credenciais

    required_fields = ["access_token", "resource_id"]
    missing = [field for field in required_fields if not credenciais.get(field)]
    if missing:
        raise HTTPException(
            status_code=400,
            detail=f"Campos obrigatórios ausentes para {tipo.value}: {', '.join(missing)}"
        )

    nome_instancia = (payload.nome_instancia or str(credenciais.get("resource_id"))).strip()
    if not nome_instancia:
        raise HTTPException(status_code=400, detail="ID do telefone/página é obrigatório.")
    return nome_instancia, credenciais


async def _validar_instancia_evolution_existe(credenciais: dict[str, Any]) -> None:
    evolution_url = str(credenciais.get("evolution_url") or "").rstrip("/") or evolution_base_url().rstrip("/")
    evolution_apikey = str(credenciais.get("evolution_apikey") or "").strip()
    evolution_instance = str(credenciais.get("evolution_instance") or "").strip()

    if not all([evolution_url, evolution_apikey, evolution_instance]):
        raise HTTPException(
            status_code=400,
            detail="Não foi possível validar a instância Evolution: credenciais incompletas.",
        )

    endpoint = f"{evolution_url}/instance/connectionState/{evolution_instance}"
    headers = {"apikey": evolution_apikey}
    timeout = httpx.Timeout(10.0, connect=5.0)

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        try:
            response = await client.get(endpoint, headers=headers)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Falha ao validar instância '{evolution_instance}' na Evolution API: {exc}",
            ) from exc

    response_text = str(response.text or "")
    response_lower = response_text.lower()

    if response.status_code == 404:
        raise HTTPException(
            status_code=400,
            detail=f"A instância '{evolution_instance}' não existe na Evolution API.",
        )

    if response.status_code >= 400:
        if "not found" in response_lower or "não encontrada" in response_lower or "nao encontrada" in response_lower:
            raise HTTPException(
                status_code=400,
                detail=f"A instância '{evolution_instance}' não existe na Evolution API.",
            )
        raise HTTPException(
            status_code=400,
            detail=(
                f"Falha ao validar instância '{evolution_instance}' na Evolution API: "
                f"status {response.status_code}."
            ),
        )


async def _testar_conectividade(tipo: TipoConexao, credenciais: dict[str, Any]) -> None:
    timeout = httpx.Timeout(8.0, connect=5.0)

    if tipo == TipoConexao.EVOLUTION:
        base_url = str(credenciais.get("evolution_url") or evolution_base_url()).rstrip("/")
        headers = {"apikey": credenciais["evolution_apikey"]}
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            try:
                response = await client.get(base_url, headers=headers)
            except Exception as exc:
                raise HTTPException(
                    status_code=400,
                    detail=f"Falha no teste de conectividade com Evolution: {exc}"
                ) from exc

        if response.status_code >= 500:
            raise HTTPException(
                status_code=400,
                detail=f"Evolution indisponível no teste de conectividade (status {response.status_code})."
            )
        return

    resource_id = credenciais["resource_id"]
    access_token = credenciais["access_token"]
    url = f"https://graph.facebook.com/v21.0/{resource_id}"
    params = {
        "fields": "id,name",
        "access_token": access_token,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            response = await client.get(url, params=params)
        except Exception as exc:
            raise HTTPException(
                status_code=400,
                detail=f"Falha no teste de conectividade com {tipo.value}: {exc}"
            ) from exc

    if response.status_code != 200:
        detail = response.text[:300] if response.text else f"status {response.status_code}"
        raise HTTPException(
            status_code=400,
            detail=f"Falha no teste de conectividade com {tipo.value}: {detail}"
        )


async def _consultar_status_conexao(conexao: Conexao) -> tuple[str, bool]:
    credenciais = dict(conexao.credenciais or {})
    timeout = httpx.Timeout(8.0, connect=5.0)

    if conexao.tipo == TipoConexao.EVOLUTION:
        evolution_url = str(credenciais.get("evolution_url") or "").rstrip("/") or evolution_base_url().rstrip("/")
        evolution_apikey = credenciais.get("evolution_apikey")
        evolution_instance = credenciais.get("evolution_instance")

        if not all([evolution_url, evolution_apikey, evolution_instance]):
            return "DISCONNECTED", False

        endpoint = f"{evolution_url}/instance/connectionState/{evolution_instance}"
        headers = {"apikey": evolution_apikey}

        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(endpoint, headers=headers)

        if response.status_code != 200:
            return "DISCONNECTED", False

        data = response.json() if response.content else {}
        state = (
            data.get("instance", {}).get("state")
            or data.get("state")
            or data.get("status")
            or data.get("connectionStatus")
            or ""
        )
        state_upper = str(state).upper()
        if state_upper == "CONNECTED":
            return "CONNECTED", True
        return state_upper or "DISCONNECTED", False

    resource_id = credenciais.get("resource_id")
    access_token = credenciais.get("access_token")
    if not all([resource_id, access_token]):
        return "DISCONNECTED", False

    url = f"https://graph.facebook.com/v21.0/{resource_id}"
    params = {
        "fields": "id",
        "access_token": access_token,
    }

    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await client.get(url, params=params)

    if response.status_code == 200:
        return "CONNECTED", True

    return "DISCONNECTED", False


async def _buscar_empresa(db: AsyncSession, empresa_id: str) -> Empresa:
    try:
        empresa_uuid = uuid.UUID(empresa_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="ID da empresa inválido.") from exc

    result = await db.execute(select(Empresa).where(Empresa.id == empresa_uuid))
    empresa = result.scalars().first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada.")
    return empresa


async def _validar_acesso_conexao_usuario(
    conexao: Conexao,
    usuario_bd: Usuario,
) -> Usuario:
    if is_root_admin_email(usuario_bd.email):
        return usuario_bd
    if is_super_admin_role(usuario_bd.role):
        return usuario_bd
    if is_admin_empresa_role(usuario_bd.role) and usuario_bd.empresa_id == conexao.empresa_id:
        return usuario_bd
    raise HTTPException(status_code=403, detail="Acesso negado para esta conexão.")


def _serialize_conexao(request: Request, empresa_id: str, conexao: Conexao) -> ConexaoResponse:
    webhook_url, webhook_path = _montar_webhook_url(request, empresa_id, conexao.tipo)
    return ConexaoResponse(
        id=conexao.id,
        empresa_id=conexao.empresa_id,
        tipo=conexao.tipo.value,
        nome_instancia=conexao.nome_instancia,
        status=conexao.status,
        credenciais_masked=_mask_credenciais(conexao.tipo, conexao.credenciais),
        webhook_url=webhook_url,
        webhook_path=webhook_path,
    )


@router.get("/", response_model=list[ConexaoResponse], status_code=status.HTTP_200_OK)
async def listar_conexoes(
    empresa_id: str,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_ia_config_access),
):
    await _buscar_empresa(db, empresa_id)
    empresa_uuid = uuid.UUID(empresa_id)
    result = await db.execute(
        select(Conexao)
        .where(Conexao.empresa_id == empresa_uuid)
        .order_by(Conexao.criado_em.desc())
    )
    conexoes = result.scalars().all()
    return [_serialize_conexao(request, empresa_id, conexao) for conexao in conexoes]


@router.post("/provision-whatsapp", status_code=status.HTTP_200_OK)
async def provisionar_whatsapp(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_ia_config_access),
):
    """
    Cria (ou reutiliza) a instância Evolution para o tenant usando EVOLUTION_API_URL/EVOLUTION_API_TOKEN
    do servidor e devolve o QR Code em Base64.
    """
    await _buscar_empresa(db, empresa_id)
    resultado = await provisionar_whatsapp_empresa(empresa_id, db)
    if not resultado.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=resultado.get("detail") or "Falha ao provisionar WhatsApp.",
        )
    return {
        "conexao_id": resultado.get("conexao_id"),
        "instance_name": resultado.get("instance_name"),
        "base64": resultado.get("base64"),
        "detail": resultado.get("detail"),
        "reaproveitada": resultado.get("reaproveitada", False),
    }


@router.post("/logout-whatsapp", status_code=status.HTTP_200_OK)
async def logout_whatsapp(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_ia_config_access),
):
    """
    Encerra a sessão WhatsApp (logout) da instância automática do tenant na Evolution,
    sem apagar a instância. Permite reconectar via QR Code logo em seguida.
    """
    await _buscar_empresa(db, empresa_id)
    resultado = await logout_evolution_whatsapp_empresa(empresa_id, db)
    if not resultado.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=resultado.get("detail") or "Não foi possível desconectar o WhatsApp.",
        )
    return {
        "detail": resultado.get("detail"),
        "instance_name": resultado.get("instance_name"),
    }


@router.post("/reset-evolution-whatsapp", status_code=status.HTTP_200_OK)
async def reset_evolution_whatsapp(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_ia_config_access),
):
    """
    Apaga na Evolution API a instância automática wa_<uuid> e remove a Conexão correspondente na BD.
    """
    await _buscar_empresa(db, empresa_id)
    resultado = await reset_evolution_whatsapp_empresa(empresa_id, db)
    if not resultado.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=resultado.get("detail") or "Não foi possível reiniciar a instância WhatsApp.",
        )
    return {
        "detail": resultado.get("detail"),
        "instance_name": resultado.get("instance_name"),
        "conexoes_removidas": resultado.get("conexoes_removidas", 0),
    }


@router.post("/", response_model=ConexaoResponse, status_code=status.HTTP_201_CREATED)
async def criar_conexao(
    empresa_id: str,
    payload: ConexaoCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_ia_config_access),
):
    await _buscar_empresa(db, empresa_id)
    empresa_uuid = uuid.UUID(empresa_id)
    tipo = _normalizar_tipo(payload.tipo)
    nome_instancia, credenciais = _validar_payload(tipo, payload)

    await _testar_conectividade(tipo, credenciais)
    if tipo == TipoConexao.EVOLUTION:
        await _validar_instancia_evolution_existe(credenciais)

    result_existente = await db.execute(
        select(Conexao).where(
            Conexao.empresa_id == empresa_uuid,
            Conexao.tipo == tipo,
            Conexao.nome_instancia == nome_instancia,
        )
    )
    existente = result_existente.scalars().first()
    if existente:
        raise HTTPException(
            status_code=409,
            detail="Já existe uma conexão desse tipo com o mesmo nome/identificador para esta empresa."
        )

    conexao = Conexao(
        empresa_id=empresa_uuid,
        tipo=tipo,
        nome_instancia=nome_instancia,
        credenciais=credenciais,
        status=_normalizar_status_conexao(payload.status, default="ativo"),
    )
    db.add(conexao)

    try:
        await db.commit()
        await db.refresh(conexao)
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=f"Erro ao criar conexão: {exc}") from exc

    return _serialize_conexao(request, empresa_id, conexao)


@router.delete("/{conexao_id}", status_code=status.HTTP_200_OK)
async def excluir_conexao(
    empresa_id: str,
    conexao_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_ia_config_access),
):
    await _buscar_empresa(db, empresa_id)

    try:
        empresa_uuid = uuid.UUID(empresa_id)
        conexao_uuid = uuid.UUID(conexao_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Identificador inválido.") from exc

    result = await db.execute(
        select(Conexao).where(
            Conexao.id == conexao_uuid,
            Conexao.empresa_id == empresa_uuid,
        )
    )
    conexao = result.scalars().first()
    if not conexao:
        raise HTTPException(status_code=404, detail="Conexão não encontrada.")

    await db.delete(conexao)
    await db.commit()
    return {"status": "success", "message": "Conexão excluída com sucesso."}


@router.put("/{conexao_id}", response_model=ConexaoResponse, status_code=status.HTTP_200_OK)
async def atualizar_conexao(
    empresa_id: str,
    conexao_id: str,
    payload: ConexaoUpdate,
    request: Request,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_ia_config_access),
):
    await _buscar_empresa(db, empresa_id)

    try:
        empresa_uuid = uuid.UUID(empresa_id)
        conexao_uuid = uuid.UUID(conexao_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Identificador inválido.") from exc

    result = await db.execute(
        select(Conexao).where(
            Conexao.id == conexao_uuid,
            Conexao.empresa_id == empresa_uuid,
        )
    )
    conexao = result.scalars().first()
    if not conexao:
        raise HTTPException(status_code=404, detail="Conexão não encontrada.")

    tipo = _normalizar_tipo(payload.tipo or conexao.tipo.value)
    credenciais_merge = dict(conexao.credenciais or {})
    credenciais_merge.update(payload.credenciais or {})

    payload_validacao = ConexaoCreate(
        tipo=tipo.value,
        nome_instancia=payload.nome_instancia if payload.nome_instancia is not None else conexao.nome_instancia,
        credenciais=credenciais_merge,
        status=payload.status if payload.status is not None else conexao.status,
    )
    nome_instancia, credenciais = _validar_payload(tipo, payload_validacao)
    await _testar_conectividade(tipo, credenciais)
    if tipo == TipoConexao.EVOLUTION:
        await _validar_instancia_evolution_existe(credenciais)

    result_existente = await db.execute(
        select(Conexao).where(
            Conexao.empresa_id == empresa_uuid,
            Conexao.tipo == tipo,
            Conexao.nome_instancia == nome_instancia,
            Conexao.id != conexao_uuid,
        )
    )
    existente = result_existente.scalars().first()
    if existente:
        raise HTTPException(
            status_code=409,
            detail="Já existe outra conexão desse tipo com o mesmo nome/identificador para esta empresa."
        )

    conexao.tipo = tipo
    conexao.nome_instancia = nome_instancia
    conexao.credenciais = credenciais
    conexao.status = _normalizar_status_conexao(payload.status or conexao.status, default="ativo")

    try:
        await db.commit()
        await db.refresh(conexao)
    except Exception as exc:
        await db.rollback()
        raise HTTPException(status_code=400, detail=f"Erro ao atualizar conexão: {exc}") from exc

    return _serialize_conexao(request, empresa_id, conexao)


@status_router.get("/{conexao_id}/status", status_code=status.HTTP_200_OK)
async def status_conexao(
    conexao_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    try:
        conexao_uuid = uuid.UUID(conexao_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Identificador inválido.") from exc

    result = await db.execute(select(Conexao).where(Conexao.id == conexao_uuid))
    conexao = result.scalars().first()
    if not conexao:
        raise HTTPException(status_code=404, detail="Conexão não encontrada.")

    await _validar_acesso_conexao_usuario(conexao, current_user)

    try:
        if conexao.tipo == TipoConexao.EVOLUTION:
            status_result = await consultar_status_conexao(conexao.id)
            if status_result.get("success"):
                status_normalizado = str(status_result.get("status") or "disconnected").lower()
                online = status_normalizado == "open"
                status_db = "ativo" if online else ("connecting" if status_normalizado == "connecting" else "disconnected")
                if _normalizar_status_conexao(conexao.status, default="") != _normalizar_status_conexao(status_db, default=""):
                    conexao.status = status_db
                    await db.commit()
                return {
                    "conexao_id": str(conexao.id),
                    "status": status_normalizado,
                    "online": online,
                }
            return {
                "conexao_id": str(conexao.id),
                "status": "disconnected",
                "online": False,
                "detail": status_result.get("detail"),
            }

        current_status, online = await _consultar_status_conexao(conexao)
        return {
            "conexao_id": str(conexao.id),
            "status": str(current_status or "").lower(),
            "online": online,
        }
    except Exception as exc:
        return {
            "conexao_id": str(conexao.id),
            "status": "disconnected",
            "online": False,
            "detail": str(exc),
        }


@status_router.get("/status/{conexao_id}", status_code=status.HTTP_200_OK)
async def status_conexao_alias(
    conexao_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    return await status_conexao(conexao_id=conexao_id, db=db, current_user=current_user)


@status_router.get("/{conexao_id}/qrcode", status_code=status.HTTP_200_OK)
async def obter_qrcode_conexao(
    conexao_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(get_current_user),
):
    try:
        conexao_uuid = uuid.UUID(conexao_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Identificador inválido.") from exc

    result = await db.execute(select(Conexao).where(Conexao.id == conexao_uuid))
    conexao = result.scalars().first()
    if not conexao:
        raise HTTPException(status_code=404, detail="Conexão não encontrada.")

    await _validar_acesso_conexao_usuario(conexao, current_user)

    if conexao.tipo != TipoConexao.EVOLUTION:
        raise HTTPException(status_code=400, detail="QR Code disponível apenas para conexões Evolution.")

    resultado = await obter_qr_code(conexao.id)
    if not resultado.get("success"):
        status_code = 409 if resultado.get("already_connected") else 400
        raise HTTPException(status_code=status_code, detail=resultado.get("detail") or "Falha ao obter QR Code.")

    return {
        "conexao_id": str(conexao.id),
        "base64": resultado.get("base64"),
        "detail": resultado.get("detail"),
    }

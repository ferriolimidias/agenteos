import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.routers.empresas import require_ia_config_access
from app.schemas import ConexaoCreate, ConexaoResponse, ConexaoUpdate
from db.database import get_db
from db.models import Conexao, Empresa, TipoConexao, Usuario, is_root_admin_email, normalize_user_role


router = APIRouter(
    prefix="/empresas/{empresa_id}/conexoes",
    tags=["Conexoes"]
)

status_router = APIRouter(
    prefix="/conexoes",
    tags=["Conexoes"]
)


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


def _validar_payload(tipo: TipoConexao, payload: ConexaoCreate) -> tuple[str, dict[str, Any]]:
    credenciais = payload.credenciais or {}

    if tipo == TipoConexao.EVOLUTION:
        required_fields = ["evolution_url", "evolution_apikey", "evolution_instance"]
        missing = [field for field in required_fields if not credenciais.get(field)]
        if missing:
            raise HTTPException(
                status_code=400,
                detail=f"Campos obrigatórios ausentes para Evolution: {', '.join(missing)}"
            )
        nome_instancia = (payload.nome_instancia or credenciais.get("evolution_instance") or "").strip()
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


async def _testar_conectividade(tipo: TipoConexao, credenciais: dict[str, Any]) -> None:
    timeout = httpx.Timeout(8.0, connect=5.0)

    if tipo == TipoConexao.EVOLUTION:
        base_url = str(credenciais["evolution_url"]).rstrip("/")
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
        evolution_url = str(credenciais.get("evolution_url") or "").rstrip("/")
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


async def _validar_acesso_conexao(
    conexao: Conexao,
    db: AsyncSession,
    x_user_id: str | None,
) -> Usuario:
    if not x_user_id:
        raise HTTPException(status_code=401, detail="Usuário não autenticado.")

    try:
        user_uuid = uuid.UUID(x_user_id)
    except ValueError as exc:
        raise HTTPException(status_code=401, detail="Identificador de usuário inválido.") from exc

    result = await db.execute(select(Usuario).where(Usuario.id == user_uuid))
    usuario_bd = result.scalars().first()
    if not usuario_bd:
        raise HTTPException(status_code=401, detail="Usuário não encontrado.")

    if is_root_admin_email(usuario_bd.email):
        return usuario_bd

    role_normalizada = normalize_user_role(usuario_bd.role)
    if role_normalizada in {"super_admin", "superadmin"}:
        return usuario_bd

    if role_normalizada in {"admin_empresa", "adminempresa"} and usuario_bd.empresa_id == conexao.empresa_id:
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
        status=(payload.status or "ativo").strip() or "ativo",
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
    conexao.status = (payload.status or conexao.status or "ativo").strip() or "ativo"

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
    x_user_id: str | None = Header(None),
):
    try:
        conexao_uuid = uuid.UUID(conexao_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Identificador inválido.") from exc

    result = await db.execute(select(Conexao).where(Conexao.id == conexao_uuid))
    conexao = result.scalars().first()
    if not conexao:
        raise HTTPException(status_code=404, detail="Conexão não encontrada.")

    await _validar_acesso_conexao(conexao, db, x_user_id)

    try:
        current_status, online = await _consultar_status_conexao(conexao)
    except Exception as exc:
        return {
            "conexao_id": str(conexao.id),
            "status": "DISCONNECTED",
            "online": False,
            "detail": str(exc),
        }

    return {
        "conexao_id": str(conexao.id),
        "status": current_status,
        "online": online,
    }

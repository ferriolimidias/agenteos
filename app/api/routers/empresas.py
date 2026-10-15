from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, status, UploadFile, File, Form
import io
import csv
import json
import logging
import secrets
import uuid
import hashlib
from datetime import datetime, time, timedelta
import pdfplumber
import traceback
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import delete, update, func, or_, text
from typing import List, Dict, Any, Optional, Literal
from fastapi.responses import Response
from sqlalchemy.orm import selectinload
from app.services.media_storage import (
    MediaValidationError,
    persist_audio_bytes,
    probe_audio_duration_ms,
    resolve_storage_path,
    sanitize_original_name,
    validate_audio_bytes,
)

from db.database import get_db, AsyncSessionLocal
from app.services.meta_attribution import dados_adicionais_for_public_response
from db.models import (
    ADMIN_EMPRESA_ROLE,
    ROOT_ADMIN_ROLE,
    CampanhaDisparo,
    CampanhaDisparoStatus,
    Empresa,
    EmpresaUnidade,
    Conhecimento,
    Usuario,
    ConhecimentoRAG,
    CRMFunil,
    CRMEtapa,
    CRMLead,
    MensagemHistorico,
    AgendaConfiguracao,
    AgendamentoLocal,
    Conexao,
    DestinosTransferencia,
    HistoricoTransferencia,
    TagGroup,
    TagCRM,
    FerramentaAPI,
    ConfigFollowUp,
    ChannelConversation,
    FollowupExecution,
    MediaAsset,
    TemplateMensagem,
    is_admin_empresa_role,
    is_meta_app_reviewer_role,
    is_root_admin_email,
    is_super_admin_role,
    normalize_user_email,
)
from pydantic import BaseModel, ConfigDict, Field

from sqlalchemy.orm.attributes import flag_modified

from app.core.security import get_password_hash
from app.services.campanha_service import extrair_variaveis_template, gerar_preview_campanha, processar_campanha_disparo
from app.services.ads_integration_service import mascarar_secret
from app.services.tag_crm_service import (
    aplicar_tags_oficiais,
    listar_tags_oficiais_ou_existentes,
    normalizar_tags,
    sync_tag_etapa_lead,
)
from app.services.meta.business_messaging_conversion_engine import (
    BusinessMessagingTrigger,
    process_business_messaging_conversion_trigger,
)
from app.services.monitor_funnel import preview_monitor_funnel, sync_monitor_funnel_tags, compile_monitor_funnel_spec
from app.services.followup_rules import (
    FOLLOWUP_ANCHOR_AD_ENTRY,
    FOLLOWUP_ANCHOR_CONTACT_CREATED,
    FOLLOWUP_ANCHOR_FIXED_DATETIME,
    FOLLOWUP_ANCHOR_FIRST_INBOUND_MESSAGE,
    FOLLOWUP_ANCHOR_LAST_AGENT_MESSAGE,
    FOLLOWUP_ANCHOR_OTHER_EVENT,
    FOLLOWUP_ANCHOR_TAG_APPLIED,
    FOLLOWUP_MODE_CONVERSATION_RESUME,
    FOLLOWUP_MODE_SCHEDULED_MESSAGE,
    FollowupRuleSpec,
)
from app.services.transferencia_service import executar_transferencia_atendimento, testar_destino_transferencia
from app.services.simulator_session import simulator_response_key
from app.services.rosangela_ai_control import get_conversation_manual_ai_state, set_conversation_ai_manual_state
from app.core.default_agents import FERRAMENTAS_SISTEMA
from app.core.llm_factory import get_embeddings_for_tenant
from app.core.secret_sanitizer import sanitize_credenciais_canais
from app.agents.tenant_ai import tenant_ai_enabled

from app.schemas import (
    EmpresaCreate,
    EmpresaResponse,
    EmpresaSetupRequest,
    EmpresaUpdate,
    EvolutionCredentials,
    IAConfigResponse,
    IAConfigUpdateRequest,
    StandardMessage,
)
from app.api.routers.auth import require_super_admin, require_tenant_access

router = APIRouter(
    prefix="/empresas",
    tags=["Empresas"]
)
logger = logging.getLogger(__name__)

admin_router = APIRouter(
    prefix="/admin/empresas",
    tags=["Admin Empresas"],
)

SIMULADOR_LEAD_ID = "ID_TESTE_SIMULADOR"
ROLES_USUARIO_EMPRESA_PERMITIDAS = {ADMIN_EMPRESA_ROLE}


class EmpresaUnidadeBase(BaseModel):
    nome_unidade: str
    endereco_completo: str
    link_google_maps: Optional[str] = None
    horario_funcionamento: Optional[str] = None
    is_matriz: bool = False


class EmpresaUnidadeCreate(EmpresaUnidadeBase):
    pass


class EmpresaUnidadeUpdate(BaseModel):
    nome_unidade: Optional[str] = None
    endereco_completo: Optional[str] = None
    link_google_maps: Optional[str] = None
    horario_funcionamento: Optional[str] = None
    is_matriz: Optional[bool] = None


class EmpresaUnidadeResponse(EmpresaUnidadeBase):
    id: str
    empresa_id: str


class EmpresaUsuarioCreate(BaseModel):
    nome: str
    email: str
    senha_temporaria: Optional[str] = None
    role: str = ADMIN_EMPRESA_ROLE
    ativo: bool = True


class EmpresaUsuarioUpdate(BaseModel):
    nome: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = None
    ativo: Optional[bool] = None


class EmpresaUsuarioResetSenha(BaseModel):
    senha_temporaria: Optional[str] = None




def _parse_uuid_or_none(value: str | None) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _parse_time_or_none(value: str | None) -> time | None:
    if value in (None, ""):
        return None
    if isinstance(value, time):
        return value.replace(second=0, microsecond=0)
    try:
        return time.fromisoformat(str(value)[:8]).replace(second=0, microsecond=0)
    except (ValueError, TypeError, AttributeError):
        return None


async def _carregar_mapa_tags_empresa(db: AsyncSession, empresa_uuid: uuid.UUID) -> tuple[dict[str, TagCRM], dict[str, TagCRM]]:
    result_tags = await db.execute(
        select(TagCRM).where(TagCRM.empresa_id == empresa_uuid)
    )
    tags = result_tags.scalars().all()
    por_id = {str(tag.id): tag for tag in tags}
    por_nome = {str(tag.nome or "").strip().lower(): tag for tag in tags if str(tag.nome or "").strip()}
    return por_id, por_nome


def _normalizar_tags_lead_para_ids(tags_brutas: Any, tags_por_id: dict[str, TagCRM], tags_por_nome: dict[str, TagCRM]) -> list[str]:
    ids: list[str] = []
    vistos: set[str] = set()
    for item in (tags_brutas or []):
        valor = str(item or "").strip()
        if not valor:
            continue
        if valor in tags_por_id:
            tag_id = valor
        else:
            tag_por_nome = tags_por_nome.get(valor.lower())
            if not tag_por_nome:
                continue
            tag_id = str(tag_por_nome.id)
        if tag_id in vistos:
            continue
        vistos.add(tag_id)
        ids.append(tag_id)
    return ids


def _montar_tags_front(tags_brutas: Any, tags_por_id: dict[str, TagCRM], tags_por_nome: dict[str, TagCRM]) -> list[dict[str, Any]]:
    tags_ids = _normalizar_tags_lead_para_ids(tags_brutas, tags_por_id, tags_por_nome)
    saida: list[dict[str, Any]] = []
    for tag_id in tags_ids:
        tag = tags_por_id.get(tag_id)
        if not tag:
            continue
        saida.append(
            {
                "id": str(tag.id),
                "nome": str(tag.nome or ""),
                "cor": str(tag.cor or "#2563eb"),
            }
        )
    return saida


async def _limpar_estado_simulador_redis(empresa_id: str, sessao_id: str = SIMULADOR_LEAD_ID) -> None:
    try:
        from app.api.main import redis_client

        if redis_client is None:
            return

        # Never delete an unscoped key: another tenant may own the same
        # caller-provided session ID.
        await redis_client.delete(simulator_response_key(empresa_id, sessao_id))
    except Exception:
        pass


def _lead_id_eh_simulador_ou_invalido(lead_id: str | None) -> bool:
    return str(lead_id or "") == SIMULADOR_LEAD_ID or _parse_uuid_or_none(lead_id) is None


def _normalizar_cor_tag(cor: str | None) -> str:
    cor_limpa = str(cor or "").strip()
    if not cor_limpa:
        return "#2563eb"
    if cor_limpa.startswith("#") and len(cor_limpa) in {4, 7}:
        return cor_limpa
    return "#2563eb"


def _mascarar_credenciais_publicas(value: Any) -> Any:
    return sanitize_credenciais_canais(value)


def _empresa_tem_openai_configurada(empresa: Empresa) -> tuple[bool, str | None]:
    direta = str(getattr(empresa, "openai_api_key", "") or "").strip()
    if direta:
        return True, None
    credenciais = getattr(empresa, "credenciais_canais", None) or {}
    if isinstance(credenciais, dict):
        for key in ("openai_api_key", "api_key_openai"):
            raw = str(credenciais.get(key) or "").strip()
            if raw:
                return True, None
    return False, None


def _serializar_empresa_segura(empresa: Empresa) -> dict[str, Any]:
    token = str(getattr(empresa, "meta_access_token", "") or "").strip()
    openai_configurada, _ = _empresa_tem_openai_configurada(empresa)
    credenciais_publicas = dict(empresa.credenciais_canais or {}) if isinstance(empresa.credenciais_canais, dict) else {}
    credenciais_publicas.pop("openai_api_key", None)
    credenciais_publicas.pop("api_key_openai", None)
    return {
        "id": empresa.id,
        "nome_empresa": empresa.nome_empresa,
        "area_atuacao": empresa.area_atuacao,
        "logo_url": empresa.logo_url,
        "credenciais_canais": _mascarar_credenciais_publicas(credenciais_publicas),
        "openai_api_key": None,
        "openai_api_key_configurada": openai_configurada,
        "ia_personalidade": getattr(empresa, "ia_personalidade", None),
        "ia_regras_negocio": getattr(empresa, "ia_regras_negocio", None),
        "conexao_disparo_id": empresa.conexao_disparo_id,
        "disparo_delay_min": empresa.disparo_delay_min if empresa.disparo_delay_min is not None else 3,
        "disparo_delay_max": empresa.disparo_delay_max if empresa.disparo_delay_max is not None else 7,
        "limite_certeza": empresa.limite_certeza if getattr(empresa, "limite_certeza", None) is not None else 0.65,
        "limite_duvida": empresa.limite_duvida if getattr(empresa, "limite_duvida", None) is not None else 0.45,
        "max_agentes_desempate": empresa.max_agentes_desempate if getattr(empresa, "max_agentes_desempate", None) is not None else 3,
        "meta_capi_ativo": bool(getattr(empresa, "meta_capi_ativo", False)),
        "meta_pixel_id": str(getattr(empresa, "meta_pixel_id", "") or "").strip() or None,
        "meta_access_token_configurado": bool(token),
        "meta_access_token_mascarado": mascarar_secret(token),
    }


def _detect_lead_columns(fieldnames: list[str]) -> tuple[str | None, str | None]:
    normalized = {str(name).strip().lower(): name for name in fieldnames if str(name).strip()}

    nome_candidates = ["nome", "nome_contato", "lead", "cliente", "contato"]
    telefone_candidates = ["telefone", "telefone_contato", "whatsapp", "celular", "phone"]

    nome_col = next((normalized[key] for key in nome_candidates if key in normalized), None)
    telefone_col = next((normalized[key] for key in telefone_candidates if key in normalized), None)
    return nome_col, telefone_col


def _load_spreadsheet_rows(file_bytes: bytes, filename: str | None) -> tuple[list[str], list[dict[str, Any]]]:
    nome_arquivo = str(filename or "").lower()

    if nome_arquivo.endswith(".xlsx"):
        try:
            from openpyxl import load_workbook
        except ImportError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Importação de Excel requer a biblioteca openpyxl instalada no servidor.",
            ) from exc

        workbook = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
        sheet = workbook.active
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            return [], []

        headers = [str(cell).strip() if cell is not None else "" for cell in rows[0]]
        parsed_rows: list[dict[str, Any]] = []
        for row in rows[1:]:
            parsed_rows.append(
                {
                    headers[index]: ("" if value is None else str(value))
                    for index, value in enumerate(row)
                    if index < len(headers) and headers[index]
                }
            )
        return headers, parsed_rows

    content = file_bytes.decode("utf-8-sig")
    sample = content[:2048]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;|\t")
    except Exception:
        dialect = csv.excel
    reader = csv.DictReader(io.StringIO(content), dialect=dialect)
    return reader.fieldnames or [], list(reader)


async def _garantir_etapa_inicial_crm(db: AsyncSession, empresa_uuid: uuid.UUID) -> uuid.UUID | None:
    result_funil = await db.execute(
        select(CRMFunil)
        .where(CRMFunil.empresa_id == empresa_uuid)
        .options(selectinload(CRMFunil.etapas))
    )
    funil = result_funil.scalars().first()

    if not funil:
        funil = CRMFunil(empresa_id=empresa_uuid, nome="Pipeline Padrão")
        db.add(funil)
        await db.flush()

    etapas = sorted(getattr(funil, "etapas", []) or [], key=lambda etapa: etapa.ordem)
    if etapas:
        return etapas[0].id

    etapa_inicial = CRMEtapa(funil_id=funil.id, nome="Entrada", tipo="entrada", ordem=1)
    db.add(etapa_inicial)
    await db.flush()
    return etapa_inicial.id


async def _inicializar_funil_padrao_empresa(db: AsyncSession, empresa_uuid: uuid.UUID) -> None:
    """Create the default CRM pipeline as part of an explicit onboarding write."""
    result = await db.execute(select(CRMFunil.id).where(CRMFunil.empresa_id == empresa_uuid).limit(1))
    if result.scalar_one_or_none() is not None:
        return

    funil = CRMFunil(empresa_id=empresa_uuid, nome="Pipeline Padrão")
    db.add(funil)
    await db.flush()
    db.add_all(
        [
            CRMEtapa(funil_id=funil.id, nome="Entrada", tipo="entrada", ordem=1),
            CRMEtapa(funil_id=funil.id, nome="Atendimento", tipo="atendimento", ordem=2),
            CRMEtapa(funil_id=funil.id, nome="Encerramento", tipo="fechamento", ordem=3),
        ]
    )
    await db.flush()


async def inicializar_dados_nova_empresa(empresa_id: uuid.UUID):
    async with AsyncSessionLocal() as session:
        try:
            # 1. Injetar as ferramentas na tabela
            for f_seed in FERRAMENTAS_SISTEMA:
                ferramenta = FerramentaAPI(
                    empresa_id=empresa_id,
                    nome_ferramenta=f_seed["nome_ferramenta"],
                    descricao_ia=f"[{f_seed['nome_exibicao']}] {f_seed['descricao_ia']}",
                    schema_parametros=f_seed["schema_parametros"],
                    metodo="GET"
                )
                session.add(ferramenta)
            await session.flush()

            res_fer = await session.execute(select(FerramentaAPI).where(FerramentaAPI.empresa_id == empresa_id))
            ferramentas_sistema = list(res_fer.scalars().all())

            print(f"Setup automático concluído para a empresa {empresa_id}")
        except Exception as e:
            await session.rollback()
            print(f"Erro ao inicializar dados fixos da empresa {empresa_id}: {e}")

@router.post("/", response_model=EmpresaResponse, status_code=status.HTTP_201_CREATED)
async def criar_empresa(
    empresa: EmpresaCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    """
    Cria uma nova Empresa no banco de dados.
    """
    nova_empresa = Empresa(
        nome_empresa=empresa.nome_empresa,
        logo_url=empresa.logo_url,
        credenciais_canais=empresa.credenciais_canais,
        openai_api_key=empresa.openai_api_key,
        ia_personalidade=empresa.ia_personalidade,
        ia_regras_negocio=empresa.ia_regras_negocio,
    )
    db.add(nova_empresa)
    try:
        await db.flush()
        await _inicializar_funil_padrao_empresa(db, nova_empresa.id)
        await db.commit()
        await db.refresh(nova_empresa)
        background_tasks.add_task(inicializar_dados_nova_empresa, nova_empresa.id)
        return _serializar_empresa_segura(nova_empresa)
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Erro ao criar empresa: {str(e)}"
        )


@router.get("/", response_model=List[EmpresaResponse], status_code=status.HTTP_200_OK)
async def listar_empresas(db: AsyncSession = Depends(get_db), _: Usuario = Depends(require_super_admin)):
    """
    Retorna a lista de todas as Empresas cadastradas.
    """
    try:
        result = await db.execute(select(Empresa))
        empresas = result.scalars().all()
        return [_serializar_empresa_segura(empresa) for empresa in empresas]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao listar empresas: {str(e)}"
        )


@router.get("/{empresa_id}", response_model=EmpresaResponse, status_code=status.HTTP_200_OK)
async def obter_empresa(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(require_tenant_access),
):
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID da empresa inválido")

    result = await db.execute(select(Empresa).where(Empresa.id == emp_uuid))
    empresa = result.scalars().first()
    if not empresa:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa não encontrada")
    return _serializar_empresa_segura(empresa)


def _gerar_senha_temporaria() -> str:
    return f"AgOS-{secrets.token_urlsafe(12)}"


def _normalizar_role_usuario_empresa(role: str | None) -> str:
    role_normalizada = str(role or ADMIN_EMPRESA_ROLE).strip().lower().replace("-", "_").replace(" ", "_")
    if role_normalizada not in ROLES_USUARIO_EMPRESA_PERMITIDAS:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Role inválida para usuário de empresa. Use admin_empresa.",
        )
    return role_normalizada


def _validar_senha_temporaria(senha: str | None) -> str:
    senha_limpa = str(senha or "").strip()
    if len(senha_limpa) < 8:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A senha temporária deve ter pelo menos 8 caracteres.",
        )
    return senha_limpa


def _serializar_usuario_empresa(usuario: Usuario) -> dict[str, Any]:
    return {
        "id": str(usuario.id),
        "empresa_id": str(usuario.empresa_id) if usuario.empresa_id else None,
        "nome": usuario.nome,
        "email": usuario.email,
        "role": usuario.role,
        "ativo": bool(usuario.ativo),
        "criado_em": None,
        "atualizado_em": None,
        "ultimo_login": None,
    }


def _impedir_mutacao_super_admin(usuario: Usuario) -> None:
    if is_root_admin_email(usuario.email) or is_super_admin_role(usuario.role):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Usuários Super Admin não podem ser alterados por este fluxo.",
        )


async def _obter_empresa_admin(db: AsyncSession, empresa_id: str) -> Empresa:
    emp_uuid = _parse_uuid_or_none(empresa_id)
    if emp_uuid is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID da empresa inválido.")

    result = await db.execute(select(Empresa).where(Empresa.id == emp_uuid))
    empresa = result.scalars().first()
    if not empresa:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa não encontrada.")
    return empresa


async def _obter_usuario_empresa_admin(db: AsyncSession, empresa_id: str, usuario_id: str) -> Usuario:
    empresa = await _obter_empresa_admin(db, empresa_id)
    usuario_uuid = _parse_uuid_or_none(usuario_id)
    if usuario_uuid is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID do usuário inválido.")

    result = await db.execute(
        select(Usuario).where(
            Usuario.id == usuario_uuid,
            Usuario.empresa_id == empresa.id,
        )
    )
    usuario = result.scalars().first()
    if not usuario:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Usuário não encontrado nesta empresa.")
    _impedir_mutacao_super_admin(usuario)
    return usuario


@admin_router.get("/{empresa_id}/usuarios", status_code=status.HTTP_200_OK)
async def listar_usuarios_empresa_admin(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    empresa = await _obter_empresa_admin(db, empresa_id)
    result = await db.execute(
        select(Usuario)
        .where(Usuario.empresa_id == empresa.id)
        .order_by(Usuario.nome.asc(), Usuario.email.asc())
    )
    usuarios = result.scalars().all()
    return [_serializar_usuario_empresa(usuario) for usuario in usuarios if not is_super_admin_role(usuario.role)]


@admin_router.post("/{empresa_id}/usuarios", status_code=status.HTTP_201_CREATED)
async def criar_usuario_empresa_admin(
    empresa_id: str,
    payload: EmpresaUsuarioCreate,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    empresa = await _obter_empresa_admin(db, empresa_id)
    email_normalizado = normalize_user_email(payload.email)
    nome = str(payload.nome or "").strip()
    role = _normalizar_role_usuario_empresa(payload.role)
    senha_temporaria = _validar_senha_temporaria(payload.senha_temporaria or _gerar_senha_temporaria())

    if not nome:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nome é obrigatório.")
    if not email_normalizado or "@" not in email_normalizado:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="E-mail inválido.")
    if is_root_admin_email(email_normalizado):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="E-mail reservado ao Super Admin.")

    result = await db.execute(select(Usuario).where(Usuario.email == email_normalizado))
    usuario_existente = result.scalars().first()

    try:
        senha_hash = get_password_hash(senha_temporaria)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if usuario_existente:
        _impedir_mutacao_super_admin(usuario_existente)
        if str(usuario_existente.empresa_id) != str(empresa.id):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="E-mail já está cadastrado em outra empresa.",
            )
        usuario_existente.nome = nome
        usuario_existente.role = role
        usuario_existente.ativo = bool(payload.ativo)
        usuario_existente.senha_hash = senha_hash
        await db.commit()
        await db.refresh(usuario_existente)
        return {
            "usuario": _serializar_usuario_empresa(usuario_existente),
            "senha_temporaria": senha_temporaria,
            "senha_exibida_uma_vez": True,
            "mensagem": "Usuário existente atualizado nesta empresa.",
        }

    novo_usuario = Usuario(
        empresa_id=empresa.id,
        nome=nome,
        email=email_normalizado,
        senha_hash=senha_hash,
        role=role,
        ativo=bool(payload.ativo),
    )
    db.add(novo_usuario)
    await db.commit()
    await db.refresh(novo_usuario)
    return {
        "usuario": _serializar_usuario_empresa(novo_usuario),
        "senha_temporaria": senha_temporaria,
        "senha_exibida_uma_vez": True,
    }


@admin_router.put("/{empresa_id}/usuarios/{usuario_id}", status_code=status.HTTP_200_OK)
async def editar_usuario_empresa_admin(
    empresa_id: str,
    usuario_id: str,
    payload: EmpresaUsuarioUpdate,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    usuario = await _obter_usuario_empresa_admin(db, empresa_id, usuario_id)

    if payload.nome is not None:
        nome = str(payload.nome or "").strip()
        if not nome:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nome é obrigatório.")
        usuario.nome = nome

    if payload.email is not None:
        email_normalizado = normalize_user_email(payload.email)
        if not email_normalizado or "@" not in email_normalizado:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="E-mail inválido.")
        if is_root_admin_email(email_normalizado):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="E-mail reservado ao Super Admin.")
        if email_normalizado != usuario.email:
            result = await db.execute(select(Usuario).where(Usuario.email == email_normalizado))
            usuario_email = result.scalars().first()
            if usuario_email:
                if str(usuario_email.empresa_id) == str(usuario.empresa_id):
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="E-mail já está cadastrado nesta empresa.",
                    )
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="E-mail já está cadastrado em outra empresa.",
                )
            usuario.email = email_normalizado

    if payload.role is not None:
        usuario.role = _normalizar_role_usuario_empresa(payload.role)

    if payload.ativo is not None:
        usuario.ativo = bool(payload.ativo)

    await db.commit()
    await db.refresh(usuario)
    return {"usuario": _serializar_usuario_empresa(usuario)}


@admin_router.post("/{empresa_id}/usuarios/{usuario_id}/reset-senha", status_code=status.HTTP_200_OK)
async def resetar_senha_usuario_empresa_admin(
    empresa_id: str,
    usuario_id: str,
    payload: EmpresaUsuarioResetSenha | None = None,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    usuario = await _obter_usuario_empresa_admin(db, empresa_id, usuario_id)
    senha_temporaria = _validar_senha_temporaria((payload.senha_temporaria if payload else None) or _gerar_senha_temporaria())
    try:
        usuario.senha_hash = get_password_hash(senha_temporaria)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    await db.commit()
    await db.refresh(usuario)
    return {
        "usuario": _serializar_usuario_empresa(usuario),
        "senha_temporaria": senha_temporaria,
        "senha_exibida_uma_vez": True,
    }


@admin_router.post("/{empresa_id}/usuarios/{usuario_id}/ativar", status_code=status.HTTP_200_OK)
async def ativar_usuario_empresa_admin(
    empresa_id: str,
    usuario_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    usuario = await _obter_usuario_empresa_admin(db, empresa_id, usuario_id)
    usuario.ativo = True
    await db.commit()
    await db.refresh(usuario)
    return {"usuario": _serializar_usuario_empresa(usuario)}


@admin_router.post("/{empresa_id}/usuarios/{usuario_id}/desativar", status_code=status.HTTP_200_OK)
async def desativar_usuario_empresa_admin(
    empresa_id: str,
    usuario_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    usuario = await _obter_usuario_empresa_admin(db, empresa_id, usuario_id)
    usuario.ativo = False
    await db.commit()
    await db.refresh(usuario)
    return {"usuario": _serializar_usuario_empresa(usuario)}


@router.get("/{empresa_id}/unidades", response_model=List[EmpresaUnidadeResponse], status_code=status.HTTP_200_OK, dependencies=[Depends(require_tenant_access)])
async def listar_unidades_empresa(empresa_id: str, db: AsyncSession = Depends(get_db)):
    empresa_uuid = _parse_uuid_or_none(empresa_id)
    if not empresa_uuid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID da empresa inválido")

    result_empresa = await db.execute(select(Empresa).where(Empresa.id == empresa_uuid))
    if not result_empresa.scalars().first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa não encontrada")

    result = await db.execute(
        select(EmpresaUnidade)
        .where(EmpresaUnidade.empresa_id == empresa_uuid)
        .order_by(EmpresaUnidade.is_matriz.desc(), EmpresaUnidade.nome_unidade.asc())
    )
    unidades = result.scalars().all()
    return [
        {
            "id": str(unidade.id),
            "empresa_id": str(unidade.empresa_id),
            "nome_unidade": str(unidade.nome_unidade or ""),
            "endereco_completo": str(unidade.endereco_completo or ""),
            "link_google_maps": str(unidade.link_google_maps) if unidade.link_google_maps else None,
            "horario_funcionamento": str(unidade.horario_funcionamento) if unidade.horario_funcionamento else None,
            "is_matriz": bool(unidade.is_matriz),
        }
        for unidade in unidades
    ]


@router.post("/{empresa_id}/unidades", response_model=EmpresaUnidadeResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_tenant_access)])
async def criar_unidade_empresa(empresa_id: str, payload: EmpresaUnidadeCreate, db: AsyncSession = Depends(get_db)):
    empresa_uuid = _parse_uuid_or_none(empresa_id)
    if not empresa_uuid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID da empresa inválido")

    result_empresa = await db.execute(select(Empresa).where(Empresa.id == empresa_uuid))
    if not result_empresa.scalars().first():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa não encontrada")

    nome_unidade = str(payload.nome_unidade or "").strip()
    endereco_completo = str(payload.endereco_completo or "").strip()
    if not nome_unidade or not endereco_completo:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="nome_unidade e endereco_completo são obrigatórios",
        )

    if payload.is_matriz:
        await db.execute(
            update(EmpresaUnidade)
            .where(EmpresaUnidade.empresa_id == empresa_uuid)
            .values(is_matriz=False)
        )

    nova_unidade = EmpresaUnidade(
        empresa_id=empresa_uuid,
        nome_unidade=nome_unidade,
        endereco_completo=endereco_completo,
        link_google_maps=str(payload.link_google_maps or "").strip() or None,
        horario_funcionamento=str(payload.horario_funcionamento or "").strip() or None,
        is_matriz=bool(payload.is_matriz),
    )
    db.add(nova_unidade)
    await db.commit()
    await db.refresh(nova_unidade)
    return {
        "id": str(nova_unidade.id),
        "empresa_id": str(nova_unidade.empresa_id),
        "nome_unidade": str(nova_unidade.nome_unidade or ""),
        "endereco_completo": str(nova_unidade.endereco_completo or ""),
        "link_google_maps": str(nova_unidade.link_google_maps) if nova_unidade.link_google_maps else None,
        "horario_funcionamento": str(nova_unidade.horario_funcionamento) if nova_unidade.horario_funcionamento else None,
        "is_matriz": bool(nova_unidade.is_matriz),
    }


@router.put("/{empresa_id}/unidades/{unidade_id}", response_model=EmpresaUnidadeResponse, status_code=status.HTTP_200_OK, dependencies=[Depends(require_tenant_access)])
async def atualizar_unidade_empresa(
    empresa_id: str,
    unidade_id: str,
    payload: EmpresaUnidadeUpdate,
    db: AsyncSession = Depends(get_db),
):
    empresa_uuid = _parse_uuid_or_none(empresa_id)
    unidade_uuid = _parse_uuid_or_none(unidade_id)
    if not empresa_uuid or not unidade_uuid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(
        select(EmpresaUnidade).where(
            EmpresaUnidade.id == unidade_uuid,
            EmpresaUnidade.empresa_id == empresa_uuid,
        )
    )
    unidade = result.scalars().first()
    if not unidade:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unidade não encontrada")

    if payload.nome_unidade is not None:
        unidade.nome_unidade = str(payload.nome_unidade).strip()
    if payload.endereco_completo is not None:
        unidade.endereco_completo = str(payload.endereco_completo).strip()
    if payload.link_google_maps is not None:
        unidade.link_google_maps = str(payload.link_google_maps).strip() or None
    if payload.horario_funcionamento is not None:
        unidade.horario_funcionamento = str(payload.horario_funcionamento).strip() or None
    if payload.is_matriz is not None:
        novo_is_matriz = bool(payload.is_matriz)
        if novo_is_matriz:
            await db.execute(
                update(EmpresaUnidade)
                .where(EmpresaUnidade.empresa_id == empresa_uuid, EmpresaUnidade.id != unidade_uuid)
                .values(is_matriz=False)
            )
        unidade.is_matriz = novo_is_matriz

    if not str(unidade.nome_unidade or "").strip() or not str(unidade.endereco_completo or "").strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="nome_unidade e endereco_completo são obrigatórios",
        )

    await db.commit()
    await db.refresh(unidade)
    return {
        "id": str(unidade.id),
        "empresa_id": str(unidade.empresa_id),
        "nome_unidade": str(unidade.nome_unidade or ""),
        "endereco_completo": str(unidade.endereco_completo or ""),
        "link_google_maps": str(unidade.link_google_maps) if unidade.link_google_maps else None,
        "horario_funcionamento": str(unidade.horario_funcionamento) if unidade.horario_funcionamento else None,
        "is_matriz": bool(unidade.is_matriz),
    }


@router.delete("/{empresa_id}/unidades/{unidade_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_tenant_access)])
async def deletar_unidade_empresa(empresa_id: str, unidade_id: str, db: AsyncSession = Depends(get_db)):
    empresa_uuid = _parse_uuid_or_none(empresa_id)
    unidade_uuid = _parse_uuid_or_none(unidade_id)
    if not empresa_uuid or not unidade_uuid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(
        select(EmpresaUnidade).where(
            EmpresaUnidade.id == unidade_uuid,
            EmpresaUnidade.empresa_id == empresa_uuid,
        )
    )
    unidade = result.scalars().first()
    if not unidade:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Unidade não encontrada")

    await db.delete(unidade)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)

# --- ROTAS DA IA CONFIGURATOR ---
# Compatibilidade: a IA Config usa a mesma política do tenant access com JWT Bearer.
# Isso evita dependência legada de X-User-Id e permite tenant/admin da própria empresa.
async def require_ia_config_access(
    empresa_id: str,
    current_user: Usuario = Depends(require_tenant_access),
):
    return current_user


def _empresa_tem_chave_openai(empresa: Empresa) -> bool:
    """
    Verdadeiro quando a empresa tem chave OpenAI BYOK configurada — seja na
    coluna dedicada `openai_api_key` ou no JSONB legado `credenciais_canais`.
    Esta flag é independente do `status_openai`, que é apenas uma bandeira
    reativa de saúde da chave (degrada após falha em runtime).
    """
    direta = str(getattr(empresa, "openai_api_key", "") or "").strip()
    if direta:
        return True
    credenciais = getattr(empresa, "credenciais_canais", None) or {}
    if isinstance(credenciais, dict):
        legada = str(credenciais.get("openai_api_key") or "").strip()
        if legada:
            return True
        alternativa = str(credenciais.get("api_key_openai") or "").strip()
        if alternativa:
            return True
    return False


@router.get("/{empresa_id}/ia-config", response_model=IAConfigResponse, status_code=status.HTTP_200_OK)
async def get_ia_config(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_ia_config_access),
):
    result = await db.execute(select(Empresa).where(Empresa.id == empresa_id))
    empresa = result.scalars().first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    ai_tag_ids = [
        str(tag_id)
        for tag_id in (
            await db.execute(select(TagCRM.id).where(TagCRM.empresa_id == empresa.id, TagCRM.ai_can_apply.is_(True)))
        ).scalars().all()
    ]
    return {
        "ia_personalidade": getattr(empresa, "ia_personalidade", None),
        "ia_regras_negocio": getattr(empresa, "ia_regras_negocio", None),
        "nome_agente": empresa.nome_agente,
        "mensagem_saudacao": empresa.mensagem_saudacao,
        "conversion_monitor_prompt": getattr(empresa, "conversion_monitor_prompt", None),
        "modelo_ia": empresa.modelo_ia,
        "followup_ativo": getattr(empresa, 'followup_ativo', False) or False,
        "limite_certeza": getattr(empresa, "limite_certeza", 0.65) if getattr(empresa, "limite_certeza", None) is not None else 0.65,
        "limite_duvida": getattr(empresa, "limite_duvida", 0.45) if getattr(empresa, "limite_duvida", None) is not None else 0.45,
        "max_agentes_desempate": getattr(empresa, "max_agentes_desempate", 3) if getattr(empresa, "max_agentes_desempate", None) is not None else 3,
        "informacoes_adicionais": getattr(empresa, 'informacoes_adicionais', None),
        "coletar_nome": getattr(empresa, 'coletar_nome', True) if getattr(empresa, 'coletar_nome', True) is not None else True,
        "telefone_notificacao": str(getattr(empresa, "telefone_notificacao", "") or "").strip() or None,
        "status_openai": str(getattr(empresa, "status_openai", "ok") or "ok"),
        "openai_configurada": _empresa_tem_openai_configurada(empresa)[0],
        # Compatibilidade de API; a fonte de verdade é TagCRM.ai_can_apply.
        "tags_ia_habilitadas": ai_tag_ids,
    }


@router.get("/{empresa_id}/monitor/funnel")
async def get_monitor_funnel(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    empresa = (await db.execute(select(Empresa).where(Empresa.id == empresa_id))).scalars().first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    spec = (getattr(empresa, "credenciais_canais", None) or {}).get("monitor_funnel_spec")
    current = compile_monitor_funnel_spec(empresa)
    return {"spec": spec or current, "current_prompt_hash": current["prompt_hash"], "stale": not spec or spec.get("prompt_hash") != current["prompt_hash"]}


@router.post("/{empresa_id}/monitor/funnel/preview")
async def preview_monitor_funnel_endpoint(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        return await preview_monitor_funnel(db, empresa_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.post("/{empresa_id}/monitor/funnel/sync")
async def sync_monitor_funnel_endpoint(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        return await sync_monitor_funnel_tags(db, empresa_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

@router.put("/{empresa_id}/ia-config", response_model=IAConfigResponse, status_code=status.HTTP_200_OK)
@router.post("/{empresa_id}/ia-config", response_model=IAConfigResponse, status_code=status.HTTP_200_OK)
async def put_ia_config(
    empresa_id: str,
    data: IAConfigUpdateRequest,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_ia_config_access),
):
    # Deprecated para agenda: horários de funcionamento devem ser atualizados em PUT /empresas/{empresa_id}/agenda.
    result = await db.execute(select(Empresa).where(Empresa.id == empresa_id))
    empresa = result.scalars().first()
    if not empresa:
        raise HTTPException(status_code=404, detail="Empresa não encontrada")
    if data.ia_personalidade is not None:
        empresa.ia_personalidade = data.ia_personalidade
    if data.ia_regras_negocio is not None:
        empresa.ia_regras_negocio = data.ia_regras_negocio
    if data.nome_agente is not None:
        empresa.nome_agente = data.nome_agente
    if data.mensagem_saudacao is not None:
        empresa.mensagem_saudacao = data.mensagem_saudacao
    if data.conversion_monitor_prompt is not None:
        empresa.conversion_monitor_prompt = data.conversion_monitor_prompt
    if data.modelo_ia is not None:
        if data.modelo_ia != str(empresa.modelo_ia or ""):
            from app.services.openai_model_catalog import classify_model, model_is_validated
            if not await model_is_validated(db, empresa.id, data.modelo_ia):
                raise HTTPException(status_code=422, detail="Valide este modelo com a credencial do tenant antes de selecioná-lo.")
            compatibility, _, _ = classify_model(data.modelo_ia)
            empresa.modelo_ia_request_profile = "legacy" if compatibility == "known_compatible" else "chat_minimal"
        empresa.modelo_ia = data.modelo_ia
    if data.followup_ativo is not None:
        empresa.followup_ativo = data.followup_ativo
    if data.limite_certeza is not None:
        empresa.limite_certeza = data.limite_certeza
    if data.limite_duvida is not None:
        empresa.limite_duvida = data.limite_duvida
    if data.max_agentes_desempate is not None:
        empresa.max_agentes_desempate = data.max_agentes_desempate
    if data.informacoes_adicionais is not None:
        empresa.informacoes_adicionais = data.informacoes_adicionais
    if data.coletar_nome is not None:
        empresa.coletar_nome = data.coletar_nome
    if data.telefone_notificacao is not None:
        empresa.telefone_notificacao = str(data.telefone_notificacao or "").strip() or None
    if data.tags_ia_habilitadas is not None:
        import uuid
        try:
            tag_ids = list(dict.fromkeys(str(uuid.UUID(str(item))) for item in data.tags_ia_habilitadas))
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="tags_ia_habilitadas deve conter UUIDs válidos")
        tags = (await db.execute(select(TagCRM).where(TagCRM.empresa_id == empresa.id, TagCRM.id.in_(tag_ids)))).scalars().all()
        if len(tags) != len(set(tag_ids)):
            raise HTTPException(status_code=400, detail="Uma ou mais tags não pertencem ao tenant")
        await db.execute(
            update(TagCRM)
            .where(TagCRM.empresa_id == empresa.id)
            .values(ai_can_apply=False)
        )
        if tag_ids:
            await db.execute(
                update(TagCRM)
                .where(TagCRM.empresa_id == empresa.id, TagCRM.id.in_(tag_ids))
                .values(ai_can_apply=True)
            )
    limite_duvida = empresa.limite_duvida if getattr(empresa, "limite_duvida", None) is not None else 0.45
    limite_certeza = empresa.limite_certeza if getattr(empresa, "limite_certeza", None) is not None else 0.65
    max_desempate = empresa.max_agentes_desempate if getattr(empresa, "max_agentes_desempate", None) is not None else 3
    if limite_duvida < 0 or limite_duvida > 1:
        raise HTTPException(status_code=400, detail="limite_duvida deve estar entre 0 e 1")
    if limite_certeza < 0 or limite_certeza > 1:
        raise HTTPException(status_code=400, detail="limite_certeza deve estar entre 0 e 1")
    if limite_duvida > limite_certeza:
        raise HTTPException(status_code=400, detail="limite_duvida não pode ser maior que limite_certeza")
    if max_desempate < 1:
        raise HTTPException(status_code=400, detail="max_agentes_desempate deve ser >= 1")
    try:
        await db.commit()
        await db.refresh(empresa)
        ai_tag_ids = [
            str(tag_id)
            for tag_id in (
                await db.execute(select(TagCRM.id).where(TagCRM.empresa_id == empresa.id, TagCRM.ai_can_apply.is_(True)))
            ).scalars().all()
        ]
        return {
            "ia_personalidade": getattr(empresa, "ia_personalidade", None),
            "ia_regras_negocio": getattr(empresa, "ia_regras_negocio", None),
            "nome_agente": empresa.nome_agente,
            "mensagem_saudacao": empresa.mensagem_saudacao,
            "conversion_monitor_prompt": getattr(empresa, "conversion_monitor_prompt", None),
            "modelo_ia": empresa.modelo_ia,
            "followup_ativo": getattr(empresa, 'followup_ativo', False) or False,
            "limite_certeza": getattr(empresa, "limite_certeza", 0.65) if getattr(empresa, "limite_certeza", None) is not None else 0.65,
            "limite_duvida": getattr(empresa, "limite_duvida", 0.45) if getattr(empresa, "limite_duvida", None) is not None else 0.45,
            "max_agentes_desempate": getattr(empresa, "max_agentes_desempate", 3) if getattr(empresa, "max_agentes_desempate", None) is not None else 3,
            "informacoes_adicionais": getattr(empresa, 'informacoes_adicionais', None),
            "coletar_nome": getattr(empresa, 'coletar_nome', True) if getattr(empresa, 'coletar_nome', True) is not None else True,
            "telefone_notificacao": str(getattr(empresa, "telefone_notificacao", "") or "").strip() or None,
            "status_openai": str(getattr(empresa, "status_openai", "ok") or "ok"),
            "openai_configurada": _empresa_tem_openai_configurada(empresa)[0],
            "tags_ia_habilitadas": ai_tag_ids,
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Erro ao atualizar: {str(e)}")


@router.put("/{empresa_id}", response_model=EmpresaResponse, status_code=status.HTTP_200_OK)
async def atualizar_empresa(
    empresa_id: str,
    data: EmpresaUpdate,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    """
    Atualiza dados básicos da Empresa (nome e área de atuação).
    """
    result = await db.execute(select(Empresa).where(Empresa.id == empresa_id))
    empresa = result.scalars().first()
    if not empresa:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa não encontrada")
    
    if data.nome_empresa is not None:
        empresa.nome_empresa = data.nome_empresa
    if data.area_atuacao is not None:
        empresa.area_atuacao = data.area_atuacao
    if data.logo_url is not None:
        empresa.logo_url = data.logo_url
    if data.ia_personalidade is not None:
        empresa.ia_personalidade = data.ia_personalidade
    if data.ia_regras_negocio is not None:
        empresa.ia_regras_negocio = data.ia_regras_negocio
    if data.disparo_delay_min is not None:
        if data.disparo_delay_min < 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="disparo_delay_min não pode ser negativo")
        empresa.disparo_delay_min = data.disparo_delay_min
    if data.disparo_delay_max is not None:
        if data.disparo_delay_max < 0:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="disparo_delay_max não pode ser negativo")
        empresa.disparo_delay_max = data.disparo_delay_max
    if data.limite_certeza is not None:
        if data.limite_certeza < 0 or data.limite_certeza > 1:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="limite_certeza deve estar entre 0 e 1")
        empresa.limite_certeza = data.limite_certeza
    if data.limite_duvida is not None:
        if data.limite_duvida < 0 or data.limite_duvida > 1:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="limite_duvida deve estar entre 0 e 1")
        empresa.limite_duvida = data.limite_duvida
    if data.max_agentes_desempate is not None:
        if data.max_agentes_desempate < 1:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="max_agentes_desempate deve ser >= 1")
        empresa.max_agentes_desempate = data.max_agentes_desempate
    if data.meta_capi_ativo is not None:
        empresa.meta_capi_ativo = data.meta_capi_ativo
    if data.meta_pixel_id is not None:
        empresa.meta_pixel_id = str(data.meta_pixel_id).strip() or None
    if data.remover_meta_access_token is True:
        empresa.meta_access_token = None
    elif data.meta_access_token is not None:
        token_limpo = str(data.meta_access_token).strip()
        if token_limpo:
            empresa.meta_access_token = token_limpo
    if data.conexao_disparo_id is not None:
        if data.conexao_disparo_id == "":
            empresa.conexao_disparo_id = None
        else:
            try:
                conexao_disparo_uuid = uuid.UUID(data.conexao_disparo_id)
            except ValueError:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="conexao_disparo_id inválido")

            result_conexao = await db.execute(
                select(Conexao).where(
                    Conexao.id == conexao_disparo_uuid,
                    Conexao.empresa_id == empresa.id,
                )
            )
            conexao = result_conexao.scalars().first()
            if not conexao:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Conexão de disparo não encontrada para esta empresa",
                )
            empresa.conexao_disparo_id = conexao.id

    delay_min = empresa.disparo_delay_min if empresa.disparo_delay_min is not None else 3
    delay_max = empresa.disparo_delay_max if empresa.disparo_delay_max is not None else 7
    if delay_min > delay_max:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="O intervalo mínimo de disparo não pode ser maior que o máximo",
        )
    limite_duvida = empresa.limite_duvida if getattr(empresa, "limite_duvida", None) is not None else 0.45
    limite_certeza = empresa.limite_certeza if getattr(empresa, "limite_certeza", None) is not None else 0.65
    if limite_duvida > limite_certeza:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="limite_duvida não pode ser maior que limite_certeza",
        )
        
    try:
        await db.commit()
        await db.refresh(empresa)
        return _serializar_empresa_segura(empresa)
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao atualizar empresa: {str(e)}")

@router.delete("/{empresa_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_super_admin)])
async def deletar_empresa(empresa_id: str, db: AsyncSession = Depends(get_db)):
    """
    Remove uma Empresa e todos os dados vinculados (via cascade configurado no banco).
    """
    import uuid
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
         raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID da empresa inválido")
         
    result = await db.execute(select(Empresa).where(Empresa.id == emp_uuid))
    empresa = result.scalars().first()
    if not empresa:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa não encontrada")
        
    try:
        await db.delete(empresa)
        await db.commit()
        return None
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao deletar empresa: {str(e)}")

@router.post("/setup", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_super_admin)])
async def setup_empresa(data: EmpresaSetupRequest, background_tasks: BackgroundTasks, db: AsyncSession = Depends(get_db)):
    """
    Cria uma nova Empresa e seu respectivo Usuário admin (admin_empresa).
    """
    print(f"Iniciando setup_empresa para: {data.nome_empresa}")
    
    # Verifica se o email já existe
    admin_email_normalizado = normalize_user_email(data.admin_email)
    result = await db.execute(select(Usuario).where(Usuario.email == admin_email_normalizado))
    if result.scalars().first():
        print(f"Setup falhou: E-mail {data.admin_email} já em uso.")
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="E-mail já está em uso por outro usuário."
        )

    try:
        print("Criando empresa...")
        nova_empresa = Empresa(
            nome_empresa=data.nome_empresa,
            area_atuacao=data.area_atuacao,
            logo_url=data.logo_url,
            ia_personalidade=data.ia_personalidade,
            ia_regras_negocio=data.ia_regras_negocio,
        )
        db.add(nova_empresa)
        await db.flush() # Para obter o ID da empresa gerado
        await _inicializar_funil_padrao_empresa(db, nova_empresa.id)
        
        print("Criando usuário admin...")
        is_root_admin = is_root_admin_email(data.admin_email)
        novo_usuario = Usuario(
            empresa_id=None if is_root_admin else nova_empresa.id,
            nome=data.admin_nome,
            email=admin_email_normalizado,
            senha_hash=get_password_hash(data.admin_senha),
            role=ROOT_ADMIN_ROLE if is_root_admin else ADMIN_EMPRESA_ROLE,
            ativo=True
        )
        db.add(novo_usuario)
        
        print("Commitando transação...")
        await db.commit()
        await db.refresh(nova_empresa)
        await db.refresh(novo_usuario)
        
        # --- NOVA LINHA DE AUTOMAÇÃO AQUI ---
        background_tasks.add_task(inicializar_dados_nova_empresa, nova_empresa.id)
        
        print(f"Setup concluído com sucesso. ID da empresa: {nova_empresa.id}")
        return {
            "mensagem": "Empresa e usuário criados com sucesso!",
            "empresa": {
                "id": str(nova_empresa.id),
                "nome": nova_empresa.nome_empresa
            },
            "usuario": {
                "id": str(novo_usuario.id),
                "email": novo_usuario.email
            }
        }
    except ValueError as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e)
        )
    except Exception as e:
        print(f"Erro durante o setup da empresa: {str(e)}")
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro banco de dados: {str(e)}"
        )


@router.get("/{empresa_id}/dashboard", dependencies=[Depends(require_tenant_access)])
async def get_dashboard_data(empresa_id: str):
    """
    Retorna dados mockados para o Dashboard do Cliente (Tenant).
    """
    return {
        "total_leads": 12,
        "agendamentos_hoje": 3,
        "status_ia": "Ativa"
    }


class RAGCreateRequest(BaseModel):
    tipo: str
    conteudo: str


class RAGDeleteRequest(BaseModel):
    source_name: str


class CRMLeadResponse(BaseModel):
    id: str
    nome_contato: str
    telefone: str | None = None
    historico_resumo: str | None = None
    tags: List[Dict[str, Any]] = Field(default_factory=list)
    dados_adicionais: Dict[str, Any] = Field(default_factory=dict)
    valor_conversao: float | None = None
    criado_em: str | None = None


class CRMEtapaResponse(BaseModel):
    id: str
    nome: str
    tipo: str | None = None
    ordem: int
    leads: List[CRMLeadResponse]


class CRMColunaTagResponse(BaseModel):
    coluna: str
    ordem: int
    leads: List[CRMLeadResponse]


class CRMFunilResponse(BaseModel):
    funil_id: str
    funil_nome: str
    etapas: List[CRMEtapaResponse]
    colunas_tags: List[CRMColunaTagResponse] = Field(default_factory=list)


class CRMEtapaUpdateRequest(BaseModel):
    nome: str | None = None
    tipo: str | None = None
    ordem: int | None = None


class CRMEtapaCreateRequest(BaseModel):
    nome: str
    tipo: str | None = "custom"
    ordem: int | None = None


class CRMLeadUpdateRequest(BaseModel):
    nome_contato: str | None = None
    telefone_contato: str | None = None
    historico_resumo: str | None = None
    etapa_id: str | None = None
    status_atendimento: str | None = None
    tags: List[str] | None = None
    dados_adicionais: Dict[str, Any] | None = None
    valor_conversao: float | None = None


class LeadAIStateUpdate(BaseModel):
    enabled: bool


class TenantAgenteResponse(BaseModel):
    id: str
    nome: str
    funcao: str | None = None
    status: str = "online"


class FollowUpBase(BaseModel):
    model_config = ConfigDict(extra="allow")

    nome: str | None = None
    ativo: bool | None = None
    mode: Literal["conversation_resume", "scheduled_message"] | None = None
    delay_value: int | None = None
    delay_unit: Literal["minutes", "hours", "days"] | None = None
    anchor_type: str | None = None
    message_content: str | None = None
    template_id: str | None = None
    cancel_on_inbound: bool | None = None
    cancel_on_human: bool | None = None
    cancel_on_opt_out: bool | None = None
    cancel_on_event_end: bool | None = None
    cancel_tag_ids: list[str] | None = None
    activation_at: str | None = None
    campaign_or_event_id: str | None = None
    variable_mapping: list[dict[str, Any]] | None = None
    needs_review: bool | None = None
    message_mode: Literal["fixed_text", "ai_contextual", "meta_template"] | None = None
    content_type: Literal["text", "audio", "text_audio"] | None = None
    media_asset_id: str | None = None
    send_as_voice: bool | None = None
    audio_caption: str | None = None
    send_window_enabled: bool | None = None
    send_window_start: str | None = None
    send_window_end: str | None = None
    timezone: str | None = None
    limit_to_free_entry_window: bool | None = None


class FollowUpCreate(FollowUpBase):
    pass


class FollowUpUpdate(BaseModel):
    model_config = ConfigDict(extra="allow")

    nome: str | None = None
    ativo: bool | None = None
    mode: Literal["conversation_resume", "scheduled_message"] | None = None
    delay_value: int | None = None
    delay_unit: Literal["minutes", "hours", "days"] | None = None
    anchor_type: str | None = None
    message_content: str | None = None
    template_id: str | None = None
    cancel_on_inbound: bool | None = None
    cancel_on_human: bool | None = None
    cancel_on_opt_out: bool | None = None
    cancel_on_event_end: bool | None = None
    cancel_tag_ids: list[str] | None = None
    activation_at: str | None = None
    campaign_or_event_id: str | None = None
    variable_mapping: list[dict[str, Any]] | None = None
    needs_review: bool | None = None
    message_mode: Literal["fixed_text", "ai_contextual", "meta_template"] | None = None
    content_type: Literal["text", "audio", "text_audio"] | None = None
    media_asset_id: str | None = None
    send_as_voice: bool | None = None
    audio_caption: str | None = None
    send_window_enabled: bool | None = None
    send_window_start: str | None = None
    send_window_end: str | None = None
    timezone: str | None = None
    limit_to_free_entry_window: bool | None = None


class FollowUpResponse(BaseModel):
    id: str
    empresa_id: str
    nome: str
    ativo: bool
    mode: Literal["conversation_resume", "scheduled_message"]
    delay_value: int
    delay_unit: Literal["minutes", "hours", "days"]
    anchor_type: str
    message_content: str
    template_id: str | None = None
    cancel_on_inbound: bool
    cancel_on_human: bool
    cancel_on_opt_out: bool
    cancel_on_event_end: bool
    cancel_tag_ids: list[str]
    activation_at: str | None = None
    campaign_or_event_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    needs_review: bool = False
    message_mode: Literal["fixed_text", "ai_contextual", "meta_template"] = "fixed_text"
    content_type: Literal["text", "audio", "text_audio"] = "text"
    media_asset_id: str | None = None
    send_as_voice: bool = False
    audio_caption: str | None = None
    send_window_enabled: bool = False
    send_window_start: str | None = None
    send_window_end: str | None = None
    timezone: str = "America/Sao_Paulo"
    limit_to_free_entry_window: bool = False
    media_asset: dict[str, Any] | None = None
    executions_total: int = 0
    executions_scheduled: int = 0
    executions_pending: int = 0
    executions_sent: int = 0
    executions_failed: int = 0
    executions_cancelled: int = 0
    next_scheduled_at: str | None = None
    last_sent_at: str | None = None
    last_error: str | None = None


async def _carregar_followup_steps(db: AsyncSession, empresa_id: uuid.UUID, followup_ids: list[uuid.UUID]) -> dict[str, dict[str, Any]]:
    if not followup_ids:
        return {}
    rows = (
        await db.execute(
            text(
                """
                select *
                from followup_sequence_steps
                where empresa_id = :empresa_id and followup_id = any(:followup_ids)
                order by step_order asc
                """
            ),
            {"empresa_id": empresa_id, "followup_ids": followup_ids},
        )
    ).mappings().all()
    output: dict[str, dict[str, Any]] = {}
    asset_ids = [row["media_asset_id"] for row in rows if row.get("media_asset_id")]
    assets_by_id: dict[str, dict[str, Any]] = {}
    if asset_ids:
        assets = (await db.execute(
            select(MediaAsset).where(MediaAsset.empresa_id == empresa_id, MediaAsset.id.in_(asset_ids))
        )).scalars().all()
        assets_by_id = {
            str(asset.id): {
                "id": str(asset.id), "empresa_id": str(asset.empresa_id), "media_type": asset.media_type,
                "original_name": sanitize_original_name(asset.original_name), "mime_type": asset.mime_type,
                "byte_size": asset.byte_size, "duration_ms": asset.duration_ms, "status": asset.status,
            }
            for asset in assets
        }
    for row in rows:
        item = dict(row)
        item["media_asset"] = assets_by_id.get(str(row.get("media_asset_id"))) if row.get("media_asset_id") else None
        output.setdefault(str(row["followup_id"]), item)
    return output


def _serialize_followup_item(item: ConfigFollowUp, step: dict[str, Any] | None = None) -> dict[str, Any]:
    spec = FollowupRuleSpec.from_row(item, step)
    response = spec.to_response_dict(
        item=item,
        created_at=item.criado_em,
        updated_at=step.get("updated_at") if isinstance(step, dict) else None,
    )
    if isinstance(step, dict) and isinstance(step.get("media_asset"), dict):
        response["media_asset"] = step["media_asset"]
    return response


async def _followup_execution_stats(db: AsyncSession, empresa_id: uuid.UUID, followup_ids: list[uuid.UUID]) -> dict[str, dict[str, Any]]:
    if not followup_ids:
        return {}
    rows = (
        await db.execute(
            text(
                """
                select
                  followup_id,
                  count(*) as executions_total,
                  count(*) filter (where status = 'scheduled') as executions_scheduled,
                  count(*) filter (where status in ('scheduled', 'queued', 'pending', 'processing', 'retry', 'locked')) as executions_pending,
                  count(*) filter (where status in ('sent', 'delivered')) as executions_sent,
                  count(*) filter (where status = 'failed') as executions_failed,
                  count(*) filter (where status = 'cancelled') as executions_cancelled,
                  min(scheduled_at) filter (where status in ('scheduled', 'queued', 'pending', 'processing', 'retry', 'locked')) as next_scheduled_at,
                  max(sent_at) as last_sent_at,
                  max(stop_reason) filter (where status in ('failed', 'cancelled') and stop_reason is not null) as last_error
                from followup_executions
                where empresa_id = :empresa_id and followup_id = any(:followup_ids)
                group by followup_id
                """
            ),
            {"empresa_id": empresa_id, "followup_ids": followup_ids},
        )
    ).mappings().all()
    output: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        for key in ("next_scheduled_at", "last_sent_at"):
            if item.get(key):
                item[key] = item[key].isoformat()
        output[str(row["followup_id"])] = item
    return output


async def _validate_followup_media(db: AsyncSession, empresa_id: uuid.UUID, spec: FollowupRuleSpec) -> None:
    if spec.content_type not in {"audio", "text_audio"}:
        return
    asset_id = _parse_uuid_or_none(spec.media_asset_id)
    if asset_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="audio_asset_required")
    result = await db.execute(
        select(MediaAsset).where(
            MediaAsset.id == asset_id,
            MediaAsset.empresa_id == empresa_id,
            MediaAsset.media_type == "audio",
            MediaAsset.status == "ready",
        )
    )
    if result.scalars().first() is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="audio_asset_not_available")


async def _upsert_followup_step(db: AsyncSession, empresa_id: uuid.UUID, followup_id: uuid.UUID, spec: FollowupRuleSpec) -> None:
    storage = spec.to_storage_payload()
    trigger_tag_id = _parse_uuid_or_none(storage.get("trigger_tag_id"))
    template_id = _parse_uuid_or_none(storage.get("template_id"))
    ad_window_mode = "cancel_on_expiry" if spec.mode == FOLLOWUP_MODE_CONVERSATION_RESUME else "template_after_expiry"
    post_send_tag_id = None
    await db.execute(
        text(
            """
            insert into followup_sequence_steps
            (id, empresa_id, followup_id, step_order, status, trigger_tag_id, delay_minutes, timezone, ad_window_mode, message_mode,
             message_free_text, content_type, media_asset_id, send_as_voice, audio_caption, template_id, variable_mapping_json, execute_conditions_json, cancel_conditions_json,
             post_send_tag_id, actions_json, send_window_enabled, send_window_start, send_window_end, limit_to_free_entry_window, created_at, updated_at)
            values
            (:id, :empresa_id, :followup_id, 1, 'active', :trigger_tag_id, :delay_minutes, :timezone, :ad_window_mode, :message_mode,
             :message_free_text, :content_type, :media_asset_id, :send_as_voice, :audio_caption, :template_id, cast(:variable_mapping as jsonb), cast(:execute_conditions as jsonb),
             cast(:cancel_conditions as jsonb), :post_send_tag_id, cast(:actions as jsonb), :send_window_enabled, :send_window_start, :send_window_end, :limit_to_free_entry_window, now(), now())
            on conflict (empresa_id, followup_id, step_order) do update set
              trigger_tag_id = excluded.trigger_tag_id,
              delay_minutes = excluded.delay_minutes,
              timezone = excluded.timezone,
              ad_window_mode = excluded.ad_window_mode,
              message_mode = excluded.message_mode,
              message_free_text = excluded.message_free_text,
              content_type = excluded.content_type,
              media_asset_id = excluded.media_asset_id,
              send_as_voice = excluded.send_as_voice,
              audio_caption = excluded.audio_caption,
              template_id = excluded.template_id,
              variable_mapping_json = excluded.variable_mapping_json,
              execute_conditions_json = excluded.execute_conditions_json,
              cancel_conditions_json = excluded.cancel_conditions_json,
              post_send_tag_id = excluded.post_send_tag_id,
              actions_json = excluded.actions_json,
              send_window_enabled = excluded.send_window_enabled,
              send_window_start = excluded.send_window_start,
              send_window_end = excluded.send_window_end,
              limit_to_free_entry_window = excluded.limit_to_free_entry_window,
              updated_at = now()
            """
        ),
        {
            "id": uuid.uuid4(),
            "empresa_id": empresa_id,
            "followup_id": followup_id,
            "trigger_tag_id": trigger_tag_id,
            "delay_minutes": max(0, int(spec.delay_minutes)),
            "timezone": str(spec.timezone or "America/Sao_Paulo"),
            "ad_window_mode": ad_window_mode,
            "message_mode": spec.message_mode,
            "message_free_text": str(spec.message_content or "").strip(),
            "content_type": spec.content_type,
            "media_asset_id": _parse_uuid_or_none(spec.media_asset_id),
            "send_as_voice": bool(spec.send_as_voice),
            "audio_caption": str(spec.audio_caption or "").strip() or None,
            "template_id": template_id,
            "variable_mapping": json.dumps([dict(item) for item in spec.variable_mapping] if spec.variable_mapping else []),
            "execute_conditions": json.dumps(storage.get("execute_conditions_json") or {}),
            "cancel_conditions": json.dumps(storage.get("cancel_conditions_json") or {}),
            "post_send_tag_id": post_send_tag_id,
            "actions": json.dumps(storage.get("actions_json") or []),
            "send_window_enabled": bool(spec.send_window_enabled),
            "send_window_start": _parse_time_or_none(spec.send_window_start),
            "send_window_end": _parse_time_or_none(spec.send_window_end),
            "limit_to_free_entry_window": bool(spec.limit_to_free_entry_window),
        },
    )


class DestinoTransferenciaBase(BaseModel):
    nome_destino: str
    contatos_destino: List[str] = Field(default_factory=list)
    instrucoes_ativacao: str | None = None


class DestinoTransferenciaCreate(DestinoTransferenciaBase):
    pass


class DestinoTransferenciaUpdate(BaseModel):
    nome_destino: str | None = None
    contatos_destino: List[str] | None = None
    instrucoes_ativacao: str | None = None


class DestinoTransferenciaResponse(DestinoTransferenciaBase):
    id: str
    criado_em: str | None = None


class HistoricoTransferenciaResponse(BaseModel):
    id: str
    criado_em: str | None = None
    lead_id: str
    lead_nome: str | None = None
    destino_id: str | None = None
    destino_nome: str | None = None
    motivo_ia: str | None = None
    resumo_enviado: str | None = None


class TransferenciaManualRequest(BaseModel):
    destino_id: str


class TagGroupBase(BaseModel):
    nome: str
    cor: str | None = None
    ordem: int = 0


class TagGroupCreate(TagGroupBase):
    pass


class TagGroupUpdate(BaseModel):
    nome: str | None = None
    cor: str | None = None
    ordem: int | None = None


class TagGroupResponse(TagGroupBase):
    id: str


TagCRMTipo = Literal["etapa_funil", "comportamento", "conversao", "operacional"]


class TagCRMBase(BaseModel):
    nome: str
    cor: str = "#2563eb"
    tipo: TagCRMTipo = "comportamento"
    ordem: int | None = None
    ativa_no_funil: bool = False
    instrucao_ia: str | None = None
    ai_can_apply: bool = False
    grupo_id: str | None = None
    disparar_conversao_ads: bool = False
    acao_fechamento: bool = False
    acao_transferir_humano: bool = False
    pausa_permanente: bool = False
    mensagem_transferencia: str | None = None


class TagCRMCreate(TagCRMBase):
    pass


class TagCRMUpdate(BaseModel):
    nome: str | None = None
    cor: str | None = None
    tipo: TagCRMTipo | None = None
    ordem: int | None = None
    ativa_no_funil: bool | None = None
    instrucao_ia: str | None = None
    ai_can_apply: bool | None = None
    grupo_id: str | None = None
    disparar_conversao_ads: bool | None = None
    acao_fechamento: bool | None = None
    acao_transferir_humano: bool | None = None
    pausa_permanente: bool | None = None
    mensagem_transferencia: str | None = None


class TagCRMResponse(TagCRMBase):
    id: str
    criado_em: str | None = None


def _serializar_tag_crm(tag: TagCRM) -> dict[str, Any]:
    return {
        "id": str(tag.id),
        "nome": tag.nome,
        "cor": tag.cor or "#2563eb",
        "tipo": str(getattr(tag, "tipo", "comportamento") or "comportamento"),
        "ordem": getattr(tag, "ordem", None),
        "ativa_no_funil": bool(getattr(tag, "ativa_no_funil", False)),
        "instrucao_ia": tag.instrucao_ia,
        "ai_can_apply": bool(getattr(tag, "ai_can_apply", False)),
        "grupo_id": str(tag.grupo_id) if tag.grupo_id else None,
        "disparar_conversao_ads": bool(tag.disparar_conversao_ads),
        "acao_fechamento": bool(tag.acao_fechamento),
        "acao_transferir_humano": bool(getattr(tag, "acao_transferir_humano", False)),
        "pausa_permanente": bool(getattr(tag, "pausa_permanente", False)),
        "mensagem_transferencia": str(getattr(tag, "mensagem_transferencia", "") or "").strip() or None,
        "criado_em": tag.criado_em.isoformat() if tag.criado_em else None,
    }


class CampanhaListaResponse(BaseModel):
    tag_id: str
    tag: str
    cor: str
    total_leads: int


class CampanhaListaLeadResponse(BaseModel):
    id: str
    nome_contato: str
    telefone_contato: str | None = None
    status: str | None = None
    tags: List[Dict[str, Any]] = Field(default_factory=list)
    historico_resumo: str | None = None
    dados_adicionais: Dict[str, Any] = Field(default_factory=dict)


class TemplateMensagemBase(BaseModel):
    nome: str
    texto_template: str


class TemplateMensagemCreate(TemplateMensagemBase):
    variaveis_esperadas: List[str] | None = None


class TemplateMensagemUpdate(BaseModel):
    nome: str | None = None
    texto_template: str | None = None
    variaveis_esperadas: List[str] | None = None


class TemplateMensagemResponse(BaseModel):
    id: str
    nome: str
    texto_template: str
    variaveis_esperadas: List[str] = Field(default_factory=list)
    criado_em: str | None = None


class CampanhaDisparoCreate(BaseModel):
    nome: str
    template_id: str
    tags_alvo: List[str] = Field(default_factory=list)
    data_agendamento: str | None = None


class CampanhaDisparoResponse(BaseModel):
    id: str
    nome: str
    template_id: str | None = None
    template_nome: str | None = None
    tags_alvo: List[str] = Field(default_factory=list)
    data_agendamento: str | None = None
    status: str
    criado_em: str | None = None


class CampanhaPreviewResponse(BaseModel):
    preview_texto: str
    total_leads: int
    usou_mock: bool

@router.get("/{empresa_id}/rag")
async def listar_conhecimento_rag(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_ia_config_access),
):
    """
    Retorna a lista de documentos lógicos da base vetorial da empresa.
    """
    try:
        result = await db.execute(
            select(
                Conhecimento.source_name.label("source_name"),
                Conhecimento.source_type.label("source_type"),
                func.count(Conhecimento.id).label("total_chunks"),
                func.min(Conhecimento.criado_em).label("criado_em"),
            )
            .where(
                Conhecimento.empresa_id == empresa_id,
                Conhecimento.source_name.isnot(None),
                func.length(func.trim(Conhecimento.source_name)) > 0,
            )
            .group_by(Conhecimento.source_name, Conhecimento.source_type)
            .order_by(func.min(Conhecimento.criado_em).desc())
        )
        documentos = result.all()
        return [
            {
                "source_name": row.source_name,
                "source_type": row.source_type,
                "total_chunks": int(row.total_chunks or 0),
                "criado_em": row.criado_em.isoformat() if row.criado_em else None,
            }
            for row in documentos
        ]
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao buscar conhecimento RAG: {str(e)}"
        )

@router.post("/{empresa_id}/rag", status_code=status.HTTP_201_CREATED)
async def adicionar_conhecimento_rag(
    empresa_id: str,
    data: RAGCreateRequest,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_ia_config_access),
):
    """
    Adiciona um novo conhecimento RAG (texto ou url) processando com Langchain e embeddings, igual ao PDF.
    """
    if data.tipo not in ["texto", "url", "pdf"]:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Tipo de conhecimento inválido.")

    texto_para_processar = ""
    label_fonte = ""
    source_name = ""

    if data.tipo == "texto":
        texto_para_processar = data.conteudo
        label_fonte = "[Texto Manual]"
        source_name = "Texto Manual"
    elif data.tipo == "url":
        try:
            import requests
            from bs4 import BeautifulSoup
            
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            response = requests.get(data.conteudo, headers=headers, timeout=10)
            response.raise_for_status()
            
            soup = BeautifulSoup(response.text, 'html.parser')
            # Remove scripts, styles, etc
            for script in soup(["script", "style", "header", "footer", "nav"]):
                script.extract()
                
            texto_para_processar = soup.get_text(separator=' ', strip=True)
            label_fonte = f"[{data.conteudo}]"
            source_name = data.conteudo
            
            if not texto_para_processar:
                 raise ValueError("Nenhum texto principal encontrado na URL.")
                 
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Erro ao extrair conteúdo da URL: {str(e)}")

    if not texto_para_processar.strip():
        raise HTTPException(status_code=400, detail="O conteúdo fornecido está vazio.")

    try:
        from langchain_text_splitters import RecursiveCharacterTextSplitter
        text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=1000,
            chunk_overlap=200,
            length_function=len,
        )
        chunks = text_splitter.split_text(texto_para_processar)
        
        embeddings_model = await get_embeddings_for_tenant(str(empresa_id), db)
        chunks_embeddings = await embeddings_model.aembed_documents(chunks)
        
        primeiro_id = None
        for i, (chunk, emb) in enumerate(zip(chunks, chunks_embeddings)):
            novo_chunk_rag = ConhecimentoRAG(
                empresa_id=empresa_id,
                tipo=data.tipo,
                conteudo=f"{label_fonte} " + chunk,
                source_name=source_name,
                source_type=data.tipo,
                status="Ativo"
            )
            db.add(novo_chunk_rag)
            await db.flush() # obtem o id
            
            if i == 0:
                primeiro_id = novo_chunk_rag.id
            
            novo_vetor = Conhecimento(
                empresa_id=empresa_id,
                conteudo=chunk,
                source_name=source_name,
                source_type=data.tipo,
                embedding=emb
            )
            db.add(novo_vetor)
            
        await db.commit()
        return {"mensagem": "Conhecimento adicionado com sucesso!", "id": str(primeiro_id)}
        
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro no processamento de RAG: {str(e)}"
        )


async def processar_rag_pdf_background(empresa_id: str, content: bytes, filename: str):
    try:
        async with AsyncSessionLocal() as db:
            source_name = filename or "arquivo.pdf"
            pdf_stream = io.BytesIO(content)
            text_content = ""
            try:
                with pdfplumber.open(pdf_stream) as pdf:
                    num_pages = len(pdf.pages)
                    print(f"Lendo PDF com pdfplumber: {source_name} ({num_pages} páginas)")
                    for page in pdf.pages:
                        page_text = page.extract_text()
                        if page_text:
                            text_content += page_text + "\n"
            except Exception as e:
                print(f"Erro ao abrir PDF com pdfplumber: {str(e)}")
                return

            if len(text_content.strip()) < 10:
                print(f"Aviso: PDF {source_name} extraiu pouco ou nenhum texto. Verifique se é uma imagem.")
                # Se for imagem, o ideal futuramente seria OCR, por hora apenas logamos.
                return

            from langchain_text_splitters import RecursiveCharacterTextSplitter
            text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200, length_function=len)
            chunks = text_splitter.split_text(text_content)

            embeddings_model = await get_embeddings_for_tenant(str(empresa_id), db)
            chunks_embeddings = await embeddings_model.aembed_documents(chunks)

            for chunk, emb in zip(chunks, chunks_embeddings):
                novo_chunk_rag = ConhecimentoRAG(
                    empresa_id=empresa_id,
                    tipo="pdf",
                    conteudo=f"[{filename}] " + chunk,
                    source_name=filename or "arquivo.pdf",
                    source_type="pdf",
                    status="Ativo"
                )
                db.add(novo_chunk_rag)

                novo_vetor = Conhecimento(
                    empresa_id=empresa_id,
                    conteudo=chunk,
                    source_name=filename or "arquivo.pdf",
                    source_type="pdf",
                    embedding=emb
                )
                db.add(novo_vetor)

            await db.commit()
            print(f"Sucesso: PDF {filename} processado!")
    except Exception as e:
        traceback.print_exc()
        print(f"Erro no processamento background do PDF {filename}: {str(e)}")


@router.post("/{empresa_id}/rag/pdf", status_code=status.HTTP_202_ACCEPTED)
async def adicionar_conhecimento_rag_pdf(
    empresa_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    _: Usuario = Depends(require_ia_config_access),
):
    """
    Cadastra e vetoriza um arquivo PDF na base de conhecimento.
    """
    if file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Arquivo deve ser um PDF.")
        
    content = await file.read()
    background_tasks.add_task(processar_rag_pdf_background, empresa_id, content, file.filename)
    return {"mensagem": "Upload concluído. O arquivo está sendo processado em segundo plano e aparecerá na lista em breve."}


@router.delete("/{empresa_id}/rag")
async def deletar_conhecimento_rag_por_source_name(
    empresa_id: str,
    payload: RAGDeleteRequest,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_ia_config_access),
):
    """
    Exclui um documento lógico da base RAG pelo source_name da empresa.
    Remove chunks vetoriais (conhecimento) e chunks de visualização (conhecimento_rag).
    """
    source_name = str(payload.source_name or "").strip()
    if not source_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="source_name é obrigatório.",
        )

    try:
        resultado_conhecimento = await db.execute(
            delete(Conhecimento).where(
                Conhecimento.empresa_id == empresa_id,
                Conhecimento.source_name == source_name,
            )
        )
        resultado_rag = await db.execute(
            delete(ConhecimentoRAG).where(
                ConhecimentoRAG.empresa_id == empresa_id,
                or_(
                    ConhecimentoRAG.source_name == source_name,
                    ConhecimentoRAG.conteudo.like(f"[{source_name}] %"),
                ),
            )
        )
        await db.commit()

        return {
            "mensagem": "Documento RAG removido com sucesso.",
            "source_name": source_name,
            "registros_conhecimento_removidos": int(resultado_conhecimento.rowcount or 0),
            "registros_conhecimento_rag_removidos": int(resultado_rag.rowcount or 0),
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao excluir conhecimento RAG: {str(e)}",
        )


@router.get("/{empresa_id}/crm", response_model=CRMFunilResponse, dependencies=[Depends(require_tenant_access)])
async def obter_crm(empresa_id: str, db: AsyncSession = Depends(get_db)):
    """
    Retorna a estrutura do funil sem alterar o estado do tenant.
    Os funis padrão são criados durante o fluxo explícito de onboarding.
    """
    try:
        # Busca o primeiro funil da empresa, carregando as etapas e os leads das etapas
        result = await db.execute(
            select(CRMFunil)
            .where(CRMFunil.empresa_id == empresa_id)
            .order_by(CRMFunil.nome.asc(), CRMFunil.id.asc())
            .options(
                selectinload(CRMFunil.etapas).selectinload(CRMEtapa.leads)
            )
        )
        funil = result.scalars().first()

        if not funil:
            return {"funil_id": "", "funil_nome": "", "etapas": [], "colunas_tags": []}

        try:
            empresa_uuid = uuid.UUID(str(empresa_id))
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

        # Ordena as etapas pela ordem (desempate estável por id)
        etapas_ordenadas = sorted(
            funil.etapas,
            key=lambda x: (int(getattr(x, "ordem", 0) or 0), str(getattr(x, "id", ""))),
        )
        tags_por_id, tags_por_nome = await _carregar_mapa_tags_empresa(db, empresa_uuid)

        def _serializar_lead(lead: CRMLead) -> dict[str, Any]:
            return {
                "id": str(lead.id),
                "nome_contato": lead.nome_contato,
                "telefone": lead.telefone_contato,
                "historico_resumo": lead.historico_resumo,
                "tags": _montar_tags_front(lead.tags, tags_por_id, tags_por_nome),
                "dados_adicionais": dados_adicionais_for_public_response(lead.dados_adicionais),
                "valor_conversao": lead.valor_conversao,
                "criado_em": lead.criado_em.isoformat() if lead.criado_em else None,
            }

        resposta: dict = {
            "funil_id": str(funil.id),
            "funil_nome": funil.nome,
            "etapas": [],
            "colunas_tags": [],
        }

        for etapa in etapas_ordenadas:
            etapa_dict = {
                "id": str(etapa.id),
                "nome": etapa.nome,
                "tipo": etapa.tipo,
                "ordem": etapa.ordem,
                "leads": [_serializar_lead(lead) for lead in etapa.leads],
            }
            resposta["etapas"].append(etapa_dict)

        result_tags_funil = await db.execute(
            select(TagCRM)
            .where(
                TagCRM.empresa_id == empresa_uuid,
                TagCRM.tipo == "etapa_funil",
                TagCRM.ativa_no_funil == True,
            )
            .order_by(TagCRM.ordem.asc(), TagCRM.nome.asc())
        )
        tags_funil_ativas = result_tags_funil.scalars().all()

        leads_por_id: dict[str, CRMLead] = {}
        for etapa in etapas_ordenadas:
            for lead in etapa.leads:
                leads_por_id[str(lead.id)] = lead

        if tags_funil_ativas:
            for indice, tag_funil in enumerate(tags_funil_ativas, start=1):
                leads_coluna: list[dict[str, Any]] = []
                tag_id = str(tag_funil.id)
                for lead in leads_por_id.values():
                    tags_ids = _normalizar_tags_lead_para_ids(lead.tags, tags_por_id, tags_por_nome)
                    if tag_id in tags_ids:
                        leads_coluna.append(_serializar_lead(lead))

                resposta["colunas_tags"].append(
                    {
                        "coluna": str(tag_funil.nome or ""),
                        "ordem": int(tag_funil.ordem) if tag_funil.ordem is not None else indice,
                        "leads": leads_coluna,
                    }
                )
        else:
            # Fallback incremental: sem tags de funil novas, mantém visualização por etapas legado.
            for etapa in etapas_ordenadas:
                resposta["colunas_tags"].append(
                    {
                        "coluna": str(etapa.nome or ""),
                        "ordem": int(getattr(etapa, "ordem", 0) or 0),
                        "leads": [_serializar_lead(lead) for lead in etapa.leads],
                    }
                )

        return resposta

    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao carregar CRM: {str(e)}"
        )


@router.get("/{empresa_id}/transferencias/destinos", response_model=List[DestinoTransferenciaResponse], dependencies=[Depends(require_tenant_access)])
async def listar_destinos_transferencia(empresa_id: str, db: AsyncSession = Depends(get_db)):
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    result = await db.execute(
        select(DestinosTransferencia)
        .where(DestinosTransferencia.empresa_id == emp_uuid)
        .order_by(DestinosTransferencia.criado_em.desc())
    )
    destinos = result.scalars().all()
    return [
        {
            "id": str(destino.id),
            "nome_destino": destino.nome_destino,
            "contatos_destino": destino.contatos_destino or [],
            "instrucoes_ativacao": destino.instrucoes_ativacao,
            "criado_em": destino.criado_em.isoformat() if destino.criado_em else None,
        }
        for destino in destinos
    ]


@router.post("/{empresa_id}/transferencias/destinos", response_model=DestinoTransferenciaResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_tenant_access)])
async def criar_destino_transferencia(
    empresa_id: str,
    data: DestinoTransferenciaCreate,
    db: AsyncSession = Depends(get_db),
):
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    contatos_normalizados = [str(contato).strip() for contato in data.contatos_destino if str(contato).strip()]
    destino = DestinosTransferencia(
        empresa_id=emp_uuid,
        nome_destino=data.nome_destino.strip(),
        contatos_destino=contatos_normalizados,
        instrucoes_ativacao=(data.instrucoes_ativacao or "").strip() or None,
    )
    db.add(destino)

    try:
        await db.commit()
        await db.refresh(destino)
        return {
            "id": str(destino.id),
            "nome_destino": destino.nome_destino,
            "contatos_destino": destino.contatos_destino or [],
            "instrucoes_ativacao": destino.instrucoes_ativacao,
            "criado_em": destino.criado_em.isoformat() if destino.criado_em else None,
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao criar destino: {str(e)}")


@router.put("/{empresa_id}/transferencias/destinos/{destino_id}", response_model=DestinoTransferenciaResponse, dependencies=[Depends(require_tenant_access)])
async def atualizar_destino_transferencia(
    empresa_id: str,
    destino_id: str,
    data: DestinoTransferenciaUpdate,
    db: AsyncSession = Depends(get_db),
):
    try:
        emp_uuid = uuid.UUID(empresa_id)
        destino_uuid = uuid.UUID(destino_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(
        select(DestinosTransferencia).where(
            DestinosTransferencia.id == destino_uuid,
            DestinosTransferencia.empresa_id == emp_uuid,
        )
    )
    destino = result.scalars().first()
    if not destino:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Destino não encontrado")

    if data.nome_destino is not None:
        destino.nome_destino = data.nome_destino.strip()
    if data.contatos_destino is not None:
        destino.contatos_destino = [str(contato).strip() for contato in data.contatos_destino if str(contato).strip()]
    if data.instrucoes_ativacao is not None:
        destino.instrucoes_ativacao = data.instrucoes_ativacao.strip() or None

    try:
        await db.commit()
        await db.refresh(destino)
        return {
            "id": str(destino.id),
            "nome_destino": destino.nome_destino,
            "contatos_destino": destino.contatos_destino or [],
            "instrucoes_ativacao": destino.instrucoes_ativacao,
            "criado_em": destino.criado_em.isoformat() if destino.criado_em else None,
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao atualizar destino: {str(e)}")


@router.delete("/{empresa_id}/transferencias/destinos/{destino_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_tenant_access)])
async def excluir_destino_transferencia(
    empresa_id: str,
    destino_id: str,
    db: AsyncSession = Depends(get_db),
):
    try:
        emp_uuid = uuid.UUID(empresa_id)
        destino_uuid = uuid.UUID(destino_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(
        select(DestinosTransferencia).where(
            DestinosTransferencia.id == destino_uuid,
            DestinosTransferencia.empresa_id == emp_uuid,
        )
    )
    destino = result.scalars().first()
    if not destino:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Destino não encontrado")

    try:
        await db.delete(destino)
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao excluir destino: {str(e)}")


@router.post("/{empresa_id}/transferencias/destinos/{destino_id}/testar", status_code=status.HTTP_200_OK, dependencies=[Depends(require_tenant_access)])
async def testar_destino_transferencia_endpoint(
    empresa_id: str,
    destino_id: str,
):
    resultado = await testar_destino_transferencia(
        empresa_id=empresa_id,
        destino_id=destino_id,
    )

    if not resultado.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=resultado.get("detail") or "Falha ao testar destino de transferência.",
        )

    return resultado


@router.get("/{empresa_id}/transferencias/historico", response_model=List[HistoricoTransferenciaResponse], dependencies=[Depends(require_tenant_access)])
async def listar_historico_transferencia(empresa_id: str, db: AsyncSession = Depends(get_db)):
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    result = await db.execute(
        select(HistoricoTransferencia)
        .where(HistoricoTransferencia.empresa_id == emp_uuid)
        .options(
            selectinload(HistoricoTransferencia.lead),
            selectinload(HistoricoTransferencia.destino),
        )
        .order_by(HistoricoTransferencia.criado_em.desc())
    )
    historicos = result.scalars().all()
    return [
        {
            "id": str(item.id),
            "criado_em": item.criado_em.isoformat() if item.criado_em else None,
            "lead_id": str(item.lead_id),
            "lead_nome": item.lead.nome_contato if item.lead else None,
            "destino_id": str(item.destino_id) if item.destino_id else None,
            "destino_nome": item.destino.nome_destino if item.destino else None,
            "motivo_ia": item.motivo_ia,
            "resumo_enviado": item.resumo_enviado,
        }
        for item in historicos
    ]


@router.get("/{empresa_id}/crm/tags/grupos", response_model=List[TagGroupResponse])
async def listar_tags_grupos(empresa_id: str, db: AsyncSession = Depends(get_db), current_user: Usuario = Depends(require_tenant_access)):
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    result = await db.execute(
        select(TagGroup)
        .where(TagGroup.empresa_id == emp_uuid)
        .order_by(TagGroup.ordem.asc(), TagGroup.nome.asc())
    )
    grupos = result.scalars().all()
    if is_meta_app_reviewer_role(current_user.role):
        test_tag = (await db.execute(select(TagCRM).where(TagCRM.empresa_id == emp_uuid, TagCRM.nome == "App Review - LeadSubmitted Test Event"))).scalars().first()
        allowed_group_id = test_tag.grupo_id if test_tag else None
        grupos = [grupo for grupo in grupos if allowed_group_id and grupo.id == allowed_group_id]
    return [
        {
            "id": str(grupo.id),
            "nome": grupo.nome,
            "cor": grupo.cor,
            "ordem": grupo.ordem,
        }
        for grupo in grupos
    ]


@router.post("/{empresa_id}/crm/tags/grupos", response_model=TagGroupResponse, status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_tenant_access)])
async def criar_tag_grupo(empresa_id: str, data: TagGroupCreate, db: AsyncSession = Depends(get_db)):
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    nome_limpo = str(data.nome or "").strip()
    if not nome_limpo:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nome do grupo é obrigatório")

    result = await db.execute(
        select(TagGroup).where(
            TagGroup.empresa_id == emp_uuid,
            TagGroup.nome.ilike(nome_limpo),
        )
    )
    existente = result.scalars().first()
    if existente:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Já existe um grupo com esse nome")

    grupo = TagGroup(
        empresa_id=emp_uuid,
        nome=nome_limpo,
        cor=_normalizar_cor_tag(data.cor) if data.cor else None,
        ordem=data.ordem or 0,
    )
    db.add(grupo)

    try:
        await db.commit()
        await db.refresh(grupo)
        return {
            "id": str(grupo.id),
            "nome": grupo.nome,
            "cor": grupo.cor,
            "ordem": grupo.ordem,
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao criar grupo de tags: {str(e)}")


@router.put("/{empresa_id}/crm/tags/grupos/{grupo_id}", response_model=TagGroupResponse, dependencies=[Depends(require_tenant_access)])
async def atualizar_tag_grupo(
    empresa_id: str,
    grupo_id: str,
    data: TagGroupUpdate,
    db: AsyncSession = Depends(get_db),
):
    try:
        emp_uuid = uuid.UUID(empresa_id)
        grupo_uuid = uuid.UUID(grupo_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(
        select(TagGroup).where(
            TagGroup.id == grupo_uuid,
            TagGroup.empresa_id == emp_uuid,
        )
    )
    grupo = result.scalars().first()
    if not grupo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grupo não encontrado")

    if data.nome is not None:
        nome_limpo = str(data.nome).strip()
        if not nome_limpo:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nome do grupo é obrigatório")
        result_dup = await db.execute(
            select(TagGroup).where(
                TagGroup.empresa_id == emp_uuid,
                TagGroup.nome.ilike(nome_limpo),
                TagGroup.id != grupo.id,
            )
        )
        if result_dup.scalars().first():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Já existe um grupo com esse nome")
        grupo.nome = nome_limpo
    if data.cor is not None:
        grupo.cor = _normalizar_cor_tag(data.cor) if data.cor else None
    if data.ordem is not None:
        grupo.ordem = data.ordem

    try:
        await db.commit()
        await db.refresh(grupo)
        return {
            "id": str(grupo.id),
            "nome": grupo.nome,
            "cor": grupo.cor,
            "ordem": grupo.ordem,
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao atualizar grupo de tags: {str(e)}")


@router.delete("/{empresa_id}/crm/tags/grupos/{grupo_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_tenant_access)])
async def excluir_tag_grupo(empresa_id: str, grupo_id: str, db: AsyncSession = Depends(get_db)):
    try:
        emp_uuid = uuid.UUID(empresa_id)
        grupo_uuid = uuid.UUID(grupo_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(
        select(TagGroup).where(
            TagGroup.id == grupo_uuid,
            TagGroup.empresa_id == emp_uuid,
        )
    )
    grupo = result.scalars().first()
    if not grupo:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grupo não encontrado")

    try:
        await db.execute(
            update(TagCRM)
            .where(TagCRM.grupo_id == grupo.id, TagCRM.empresa_id == emp_uuid)
            .values(grupo_id=None)
        )
        await db.delete(grupo)
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao excluir grupo de tags: {str(e)}")


@router.get("/{empresa_id}/crm/tags/oficiais", response_model=List[TagCRMResponse])
async def listar_tags_crm_oficiais(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    current_user: Usuario = Depends(require_tenant_access),
):
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    result = await db.execute(
        select(TagCRM)
        .where(TagCRM.empresa_id == emp_uuid)
        .order_by(TagCRM.nome.asc())
    )
    tags = result.scalars().all()
    if is_meta_app_reviewer_role(current_user.role):
        tags = [tag for tag in tags if tag.nome == "App Review - LeadSubmitted Test Event"]
    return [_serializar_tag_crm(tag) for tag in tags]


@router.post("/{empresa_id}/crm/tags/oficiais", response_model=TagCRMResponse, status_code=status.HTTP_201_CREATED)
async def criar_tag_crm_oficial(
    empresa_id: str,
    data: TagCRMCreate,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    nome_limpo = str(data.nome or "").strip()
    if not nome_limpo:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nome da tag é obrigatório")

    result = await db.execute(
        select(TagCRM).where(
            TagCRM.empresa_id == emp_uuid,
            TagCRM.nome.ilike(nome_limpo),
        )
    )
    existente = result.scalars().first()
    if existente:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Já existe uma tag oficial com esse nome")

    grupo_uuid = None
    if data.grupo_id:
        try:
            grupo_uuid = uuid.UUID(str(data.grupo_id))
        except ValueError:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="grupo_id inválido")
        result_grupo = await db.execute(
            select(TagGroup).where(
                TagGroup.id == grupo_uuid,
                TagGroup.empresa_id == emp_uuid,
            )
        )
        if not result_grupo.scalars().first():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grupo de tags não encontrado")

    tag = TagCRM(
        empresa_id=emp_uuid,
        grupo_id=grupo_uuid,
        nome=nome_limpo,
        cor=_normalizar_cor_tag(data.cor),
        tipo=str(data.tipo or "comportamento"),
        ordem=data.ordem,
        ativa_no_funil=bool(data.ativa_no_funil),
        instrucao_ia=(data.instrucao_ia or "").strip() or None,
        ai_can_apply=bool(data.ai_can_apply),
        disparar_conversao_ads=bool(data.disparar_conversao_ads),
        acao_fechamento=bool(data.acao_fechamento),
        acao_transferir_humano=bool(data.acao_transferir_humano),
        pausa_permanente=bool(data.pausa_permanente),
        mensagem_transferencia=(data.mensagem_transferencia or "").strip() or None,
    )
    db.add(tag)

    try:
        await db.flush()
        await db.commit()
        await db.refresh(tag)
        return _serializar_tag_crm(tag)
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao criar tag oficial: {str(e)}")


@router.put("/{empresa_id}/crm/tags/oficiais/{tag_id}", response_model=TagCRMResponse)
async def atualizar_tag_crm_oficial(
    empresa_id: str,
    tag_id: str,
    data: TagCRMUpdate,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        emp_uuid = uuid.UUID(empresa_id)
        tag_uuid = uuid.UUID(tag_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(
        select(TagCRM).where(
            TagCRM.id == tag_uuid,
            TagCRM.empresa_id == emp_uuid,
        )
    )
    tag = result.scalars().first()
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag oficial não encontrada")

    if data.nome is not None:
        nome_limpo = str(data.nome).strip()
        if not nome_limpo:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nome da tag é obrigatório")
        result_dup = await db.execute(
            select(TagCRM).where(
                TagCRM.empresa_id == emp_uuid,
                TagCRM.nome.ilike(nome_limpo),
                TagCRM.id != tag.id,
            )
        )
        if result_dup.scalars().first():
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Já existe uma tag oficial com esse nome")
        tag.nome = nome_limpo
    if data.cor is not None:
        tag.cor = _normalizar_cor_tag(data.cor)
    if data.tipo is not None:
        tag.tipo = str(data.tipo or "comportamento")
    if data.ordem is not None:
        tag.ordem = data.ordem
    if data.ativa_no_funil is not None:
        tag.ativa_no_funil = bool(data.ativa_no_funil)
    if data.instrucao_ia is not None:
        tag.instrucao_ia = str(data.instrucao_ia).strip() or None
    if data.ai_can_apply is not None:
        tag.ai_can_apply = bool(data.ai_can_apply)
    if data.disparar_conversao_ads is not None:
        tag.disparar_conversao_ads = bool(data.disparar_conversao_ads)
    if data.acao_fechamento is not None:
        tag.acao_fechamento = bool(data.acao_fechamento)
    if data.acao_transferir_humano is not None:
        tag.acao_transferir_humano = bool(data.acao_transferir_humano)
    if data.pausa_permanente is not None:
        tag.pausa_permanente = bool(data.pausa_permanente)
    if data.mensagem_transferencia is not None:
        tag.mensagem_transferencia = str(data.mensagem_transferencia).strip() or None
    if data.grupo_id is not None:
        if data.grupo_id == "":
            tag.grupo_id = None
        else:
            try:
                grupo_uuid = uuid.UUID(str(data.grupo_id))
            except ValueError:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="grupo_id inválido")
            result_grupo = await db.execute(
                select(TagGroup).where(
                    TagGroup.id == grupo_uuid,
                    TagGroup.empresa_id == emp_uuid,
                )
            )
            grupo = result_grupo.scalars().first()
            if not grupo:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Grupo de tags não encontrado")
            tag.grupo_id = grupo.id

    try:
        await db.commit()
        await db.refresh(tag)
        return _serializar_tag_crm(tag)
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao atualizar tag oficial: {str(e)}")


@router.delete("/{empresa_id}/crm/tags/oficiais/{tag_id}", status_code=status.HTTP_204_NO_CONTENT)
async def excluir_tag_crm_oficial(
    empresa_id: str,
    tag_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        emp_uuid = uuid.UUID(empresa_id)
        tag_uuid = uuid.UUID(tag_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(
        select(TagCRM).where(
            TagCRM.id == tag_uuid,
            TagCRM.empresa_id == emp_uuid,
        )
    )
    tag = result.scalars().first()
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag oficial não encontrada")

    try:
        await db.delete(tag)
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao excluir tag oficial: {str(e)}")


@router.get("/{empresa_id}/crm/tags", response_model=List[str], dependencies=[Depends(require_tenant_access)])
async def listar_tags_crm(empresa_id: str, db: AsyncSession = Depends(get_db)):
    try:
        return await listar_tags_oficiais_ou_existentes(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")


@router.get("/{empresa_id}/campanhas/templates", response_model=List[TemplateMensagemResponse])
async def listar_templates_mensagem(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    result = await db.execute(
        select(TemplateMensagem)
        .where(TemplateMensagem.empresa_id == emp_uuid)
        .order_by(TemplateMensagem.criado_em.desc())
    )
    templates = result.scalars().all()
    return [
        {
            "id": str(template.id),
            "nome": template.nome,
            "texto_template": template.texto_template,
            "variaveis_esperadas": template.variaveis_esperadas or [],
            "criado_em": template.criado_em.isoformat() if template.criado_em else None,
        }
        for template in templates
    ]


@router.post("/{empresa_id}/campanhas/templates", response_model=TemplateMensagemResponse, status_code=status.HTTP_201_CREATED)
async def criar_template_mensagem(
    empresa_id: str,
    data: TemplateMensagemCreate,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    variaveis = data.variaveis_esperadas or extrair_variaveis_template(data.texto_template)
    template = TemplateMensagem(
        empresa_id=emp_uuid,
        nome=data.nome.strip(),
        texto_template=data.texto_template,
        variaveis_esperadas=[str(item).strip() for item in variaveis if str(item).strip()],
    )
    db.add(template)

    try:
        await db.commit()
        await db.refresh(template)
        return {
            "id": str(template.id),
            "nome": template.nome,
            "texto_template": template.texto_template,
            "variaveis_esperadas": template.variaveis_esperadas or [],
            "criado_em": template.criado_em.isoformat() if template.criado_em else None,
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao criar template: {str(e)}")


@router.put("/{empresa_id}/campanhas/templates/{template_id}", response_model=TemplateMensagemResponse)
async def atualizar_template_mensagem(
    empresa_id: str,
    template_id: str,
    data: TemplateMensagemUpdate,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        emp_uuid = uuid.UUID(empresa_id)
        template_uuid = uuid.UUID(template_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(
        select(TemplateMensagem).where(
            TemplateMensagem.id == template_uuid,
            TemplateMensagem.empresa_id == emp_uuid,
        )
    )
    template = result.scalars().first()
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template não encontrado")

    texto_template_final = data.texto_template if data.texto_template is not None else template.texto_template
    if data.nome is not None:
        template.nome = data.nome.strip()
    if data.texto_template is not None:
        template.texto_template = data.texto_template
    if data.variaveis_esperadas is not None:
        template.variaveis_esperadas = [str(item).strip() for item in data.variaveis_esperadas if str(item).strip()]
    else:
        template.variaveis_esperadas = extrair_variaveis_template(texto_template_final)

    try:
        await db.commit()
        await db.refresh(template)
        return {
            "id": str(template.id),
            "nome": template.nome,
            "texto_template": template.texto_template,
            "variaveis_esperadas": template.variaveis_esperadas or [],
            "criado_em": template.criado_em.isoformat() if template.criado_em else None,
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao atualizar template: {str(e)}")


@router.delete("/{empresa_id}/campanhas/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
async def excluir_template_mensagem(
    empresa_id: str,
    template_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        emp_uuid = uuid.UUID(empresa_id)
        template_uuid = uuid.UUID(template_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(
        select(TemplateMensagem).where(
            TemplateMensagem.id == template_uuid,
            TemplateMensagem.empresa_id == emp_uuid,
        )
    )
    template = result.scalars().first()
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template não encontrado")

    try:
        await db.delete(template)
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao excluir template: {str(e)}")


@router.get("/{empresa_id}/campanhas", response_model=List[CampanhaDisparoResponse])
async def listar_campanhas_disparo(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    result = await db.execute(
        select(CampanhaDisparo)
        .where(CampanhaDisparo.empresa_id == emp_uuid)
        .options(selectinload(CampanhaDisparo.template))
        .order_by(CampanhaDisparo.criado_em.desc())
    )
    campanhas = result.scalars().all()
    return [
        {
            "id": str(campanha.id),
            "nome": campanha.nome,
            "template_id": str(campanha.template_id) if campanha.template_id else None,
            "template_nome": campanha.template.nome if campanha.template else None,
            "tags_alvo": campanha.tags_alvo or [],
            "data_agendamento": campanha.data_agendamento.isoformat() if campanha.data_agendamento else None,
            "status": campanha.status.value if hasattr(campanha.status, "value") else str(campanha.status),
            "criado_em": campanha.criado_em.isoformat() if campanha.criado_em else None,
        }
        for campanha in campanhas
    ]


@router.get("/{empresa_id}/campanhas/listas", response_model=List[CampanhaListaResponse])
async def listar_listas_campanhas(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    result_tags = await db.execute(
        select(TagCRM)
        .where(TagCRM.empresa_id == emp_uuid)
        .order_by(TagCRM.nome.asc())
    )
    tags_oficiais = result_tags.scalars().all()
    tags_por_id = {str(tag.id): tag for tag in tags_oficiais}
    tags_por_nome = {str(tag.nome or "").strip().lower(): tag for tag in tags_oficiais if str(tag.nome or "").strip()}

    result_leads = await db.execute(
        select(CRMLead.tags).where(CRMLead.empresa_id == emp_uuid)
    )
    leads_tags = result_leads.scalars().all()

    def _contar(tag_id: str) -> int:
        total = 0
        for tags in leads_tags:
            tags_ids = set(_normalizar_tags_lead_para_ids(tags, tags_por_id, tags_por_nome))
            if tag_id in tags_ids:
                total += 1
        return total

    return [
        {
            "tag_id": str(tag.id),
            "tag": tag.nome,
            "cor": tag.cor or "#2563eb",
            "total_leads": _contar(str(tag.id)),
        }
        for tag in tags_oficiais
    ]


@router.get("/{empresa_id}/campanhas/listas/{tag_id}/leads", response_model=List[CampanhaListaLeadResponse])
async def listar_leads_da_lista_campanha(
    empresa_id: str,
    tag_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        emp_uuid = uuid.UUID(empresa_id)
        tag_uuid = uuid.UUID(tag_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result_tag = await db.execute(
        select(TagCRM).where(
            TagCRM.id == tag_uuid,
            TagCRM.empresa_id == emp_uuid,
        )
    )
    tag = result_tag.scalars().first()
    if not tag:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tag oficial não encontrada")

    result_leads = await db.execute(
        select(CRMLead)
        .where(CRMLead.empresa_id == emp_uuid)
        .options(selectinload(CRMLead.etapa))
        .order_by(CRMLead.criado_em.desc())
    )
    leads = result_leads.scalars().all()
    tags_por_id, tags_por_nome = await _carregar_mapa_tags_empresa(db, emp_uuid)
    tag_id_str = str(tag.id)

    return [
        {
            "id": str(lead.id),
            "nome_contato": lead.nome_contato,
            "telefone_contato": lead.telefone_contato,
            "status": lead.etapa.nome if lead.etapa else None,
            "tags": _montar_tags_front(lead.tags, tags_por_id, tags_por_nome),
            "historico_resumo": lead.historico_resumo,
            "dados_adicionais": dados_adicionais_for_public_response(lead.dados_adicionais),
        }
        for lead in leads
        if tag_id_str in set(_normalizar_tags_lead_para_ids(lead.tags, tags_por_id, tags_por_nome))
    ]


@router.get("/{empresa_id}/campanhas/preview", response_model=CampanhaPreviewResponse)
async def preview_campanha_disparo(
    empresa_id: str,
    template_id: str,
    tag: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        preview = await gerar_preview_campanha(
            session=db,
            empresa_id=empresa_id,
            template_id=template_id,
            tag=tag,
        )
        return preview
    except ValueError as e:
        mensagem = str(e)
        status_code = status.HTTP_404_NOT_FOUND if "não encontrado" in mensagem.lower() else status.HTTP_400_BAD_REQUEST
        raise HTTPException(status_code=status_code, detail=mensagem)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao gerar pré-visualização: {str(e)}",
        )


@router.post("/{empresa_id}/campanhas", response_model=CampanhaDisparoResponse, status_code=status.HTTP_201_CREATED)
async def criar_campanha_disparo(
    empresa_id: str,
    data: CampanhaDisparoCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    raise HTTPException(
        status_code=status.HTTP_409_CONFLICT,
        detail=(
            "O endpoint legado de disparo direto está desativado por segurança. "
            "Use /campaign-central/campaigns/prepare para preparar campanha em rascunho sem envio."
        ),
    )

    try:
        emp_uuid = uuid.UUID(empresa_id)
        template_uuid = uuid.UUID(data.template_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result_empresa = await db.execute(select(Empresa).where(Empresa.id == emp_uuid))
    empresa = result_empresa.scalars().first()
    if not empresa:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Empresa não encontrada")
    if not empresa.conexao_disparo_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Configure a conexão padrão para disparos antes de criar uma campanha",
        )

    result_template = await db.execute(
        select(TemplateMensagem).where(
            TemplateMensagem.id == template_uuid,
            TemplateMensagem.empresa_id == emp_uuid,
        )
    )
    template = result_template.scalars().first()
    if not template:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Template não encontrado")

    tags_alvo = [str(tag).strip() for tag in (data.tags_alvo or []) if str(tag).strip()]
    if not tags_alvo:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Selecione ao menos uma tag alvo")

    result_leads = await db.execute(
        select(CRMLead.tags).where(CRMLead.empresa_id == emp_uuid)
    )
    total_leads = 0
    tags_alvo_normalizadas = {tag.lower() for tag in tags_alvo}
    for tags in result_leads.scalars().all():
        if not isinstance(tags, list):
            continue
        tags_lead = {str(tag).strip().lower() for tag in tags if str(tag).strip()}
        if tags_alvo_normalizadas.intersection(tags_lead):
            total_leads += 1

    if total_leads == 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Não há leads com a tag selecionada para este disparo",
        )

    campanha = CampanhaDisparo(
        empresa_id=emp_uuid,
        nome=data.nome.strip(),
        template_id=template.id,
        tags_alvo=tags_alvo,
        data_agendamento=datetime.fromisoformat(data.data_agendamento) if data.data_agendamento else datetime.utcnow(),
        status=CampanhaDisparoStatus.PENDENTE,
    )
    db.add(campanha)

    try:
        await db.commit()
        await db.refresh(campanha)
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao criar campanha: {str(e)}")

    background_tasks.add_task(processar_campanha_disparo, str(campanha.id), str(emp_uuid))

    return {
        "id": str(campanha.id),
        "nome": campanha.nome,
        "template_id": str(campanha.template_id) if campanha.template_id else None,
        "template_nome": template.nome,
        "tags_alvo": campanha.tags_alvo or [],
        "data_agendamento": campanha.data_agendamento.isoformat() if campanha.data_agendamento else None,
        "status": campanha.status.value if hasattr(campanha.status, "value") else str(campanha.status),
        "criado_em": campanha.criado_em.isoformat() if campanha.criado_em else None,
    }


@router.post("/{empresa_id}/crm/etapas", status_code=status.HTTP_201_CREATED, dependencies=[Depends(require_tenant_access)])
async def criar_etapa_crm(
    empresa_id: str,
    data: CRMEtapaCreateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Cria uma nova etapa (coluna) no funil principal da empresa (`CRMEtapa`).
    """
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    result = await db.execute(
        select(CRMFunil)
        .where(CRMFunil.empresa_id == empresa_id)
        .order_by(CRMFunil.nome.asc(), CRMFunil.id.asc())
        .options(selectinload(CRMFunil.etapas))
    )
    funil = result.scalars().first()
    if not funil:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Nenhum funil CRM encontrado para esta empresa. Abra o CRM uma vez para inicializar o pipeline.",
        )

    nome_limpo = (data.nome or "").strip()
    if not nome_limpo:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Nome da etapa é obrigatório.")

    ordem_val = data.ordem
    if ordem_val is None:
        max_ordem = 0
        for e in funil.etapas or []:
            try:
                max_ordem = max(max_ordem, int(e.ordem or 0))
            except (TypeError, ValueError):
                max_ordem = max(max_ordem, 0)
        ordem_val = max_ordem + 1

    tipo_val = (data.tipo or "custom").strip() or "custom"
    nova = CRMEtapa(funil_id=funil.id, nome=nome_limpo, tipo=tipo_val, ordem=int(ordem_val))
    db.add(nova)
    try:
        await db.commit()
        await db.refresh(nova)
        return {
            "id": str(nova.id),
            "nome": nova.nome,
            "tipo": nova.tipo,
            "ordem": nova.ordem,
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao criar etapa: {str(e)}",
        )


@router.delete("/{empresa_id}/crm/etapas/{etapa_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_tenant_access)])
async def excluir_etapa_crm(
    empresa_id: str,
    etapa_id: str,
    db: AsyncSession = Depends(get_db),
):
    """
    Remove uma etapa do funil. Leads nessa etapa são movidos para a etapa restante com menor `ordem`.
    """
    try:
        emp_uuid = uuid.UUID(empresa_id)
        etapa_uuid = uuid.UUID(etapa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(
        select(CRMEtapa)
        .join(CRMFunil, CRMEtapa.funil_id == CRMFunil.id)
        .where(CRMEtapa.id == etapa_uuid, CRMFunil.empresa_id == emp_uuid)
        .options(selectinload(CRMEtapa.funil).selectinload(CRMFunil.etapas))
    )
    etapa = result.scalars().first()
    if not etapa:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Etapa não encontrada")

    todas = list(etapa.funil.etapas or [])
    if len(todas) <= 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Não é possível excluir a única etapa do funil.",
        )

    outras = [e for e in todas if e.id != etapa.id]
    alvo = sorted(outras, key=lambda x: (int(x.ordem or 0), str(x.id)))[0]

    await db.execute(
        update(CRMLead).where(CRMLead.etapa_id == etapa_uuid, CRMLead.empresa_id == emp_uuid).values(etapa_id=alvo.id)
    )
    await db.delete(etapa)
    try:
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao excluir etapa: {str(e)}",
        )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/{empresa_id}/crm/etapas/{etapa_id}", status_code=status.HTTP_200_OK, dependencies=[Depends(require_tenant_access)])
async def atualizar_etapa_crm(
    empresa_id: str,
    etapa_id: str,
    data: CRMEtapaUpdateRequest,
    db: AsyncSession = Depends(get_db)
):
    """
    Atualiza dados de uma etapa do CRM (nome, tipo e ordem).
    """
    try:
        emp_uuid = uuid.UUID(empresa_id)
        etapa_uuid = uuid.UUID(etapa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(
        select(CRMEtapa)
        .join(CRMFunil, CRMEtapa.funil_id == CRMFunil.id)
        .where(CRMEtapa.id == etapa_uuid, CRMFunil.empresa_id == emp_uuid)
    )
    etapa = result.scalars().first()
    if not etapa:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Etapa não encontrada")

    if data.nome is not None:
        etapa.nome = data.nome
    if data.tipo is not None:
        etapa.tipo = data.tipo
    if data.ordem is not None:
        etapa.ordem = data.ordem

    try:
        await db.commit()
        await db.refresh(etapa)
        return {
            "id": str(etapa.id),
            "nome": etapa.nome,
            "tipo": etapa.tipo,
            "ordem": etapa.ordem
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao atualizar etapa: {str(e)}")


@router.put("/{empresa_id}/crm/leads/{lead_id}", status_code=status.HTTP_200_OK)
async def atualizar_lead_crm(
    empresa_id: str,
    lead_id: str,
    data: CRMLeadUpdateRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(require_tenant_access),
):
    """
    Atualiza dados de um lead do CRM, incluindo dados_adicionais.
    """
    if _lead_id_eh_simulador_ou_invalido(lead_id):
        return {
            "id": lead_id,
            "nome_contato": data.nome_contato or "[Simulador]",
            "telefone_contato": data.telefone_contato or SIMULADOR_LEAD_ID,
            "historico_resumo": data.historico_resumo or "",
            "etapa_id": data.etapa_id or None,
            "status_atendimento": data.status_atendimento or "aberto",
            "tags": data.tags or [],
            "dados_adicionais": data.dados_adicionais or {},
            "valor_conversao": data.valor_conversao or 0,
        }

    try:
        emp_uuid = uuid.UUID(empresa_id)
        lead_uuid = uuid.UUID(lead_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(select(CRMLead).where(CRMLead.id == lead_uuid, CRMLead.empresa_id == emp_uuid))
    lead = result.scalars().first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado")

    mover_para_fechado = False
    etapa_alterada_no_payload = False

    if data.etapa_id is not None:
        etapa_alterada_no_payload = True
        if data.etapa_id == "":
            lead.etapa_id = None
        else:
            try:
                etapa_uuid = uuid.UUID(data.etapa_id)
            except ValueError:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="etapa_id inválido")

            result_etapa = await db.execute(
                select(CRMEtapa)
                .join(CRMFunil, CRMEtapa.funil_id == CRMFunil.id)
                .where(CRMEtapa.id == etapa_uuid, CRMFunil.empresa_id == emp_uuid)
            )
            etapa = result_etapa.scalars().first()
            if not etapa:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Etapa não encontrada para esta empresa")
            lead.etapa_id = etapa.id

    if data.nome_contato is not None:
        lead.nome_contato = data.nome_contato
        dados_adicionais = dict(lead.dados_adicionais or {})
        dados_adicionais["nome_origem"] = "manual"
        dados_adicionais["nome_editado_manual"] = True
        lead.dados_adicionais = dados_adicionais
    if data.telefone_contato is not None:
        lead.telefone_contato = data.telefone_contato
    if data.historico_resumo is not None:
        lead.historico_resumo = data.historico_resumo
    if data.status_atendimento is not None:
        status_limpo = str(data.status_atendimento or "").strip().lower()
        if status_limpo not in {"aberto", "concluido"}:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="status_atendimento inválido")
        lead.status_atendimento = status_limpo
        if status_limpo == "concluido":
            mover_para_fechado = True
    tags_novas_ids: list[str] = []
    if data.tags is not None:
        tags_atuais_ids = {str(tag).strip() for tag in (lead.tags or []) if str(tag).strip()}
        tags_limpas = [str(tag).strip() for tag in data.tags if str(tag).strip()]
        tags_por_id, tags_por_nome = await _carregar_mapa_tags_empresa(db, emp_uuid)
        tags_ids_normalizadas = _normalizar_tags_lead_para_ids(tags_limpas, tags_por_id, tags_por_nome)
        resultado_tags = await aplicar_tags_oficiais(
            db, lead=lead, tag_values=tags_ids_normalizadas, source="manual",
            actor_user_id=getattr(usuario, "id", None), source_reference_id="inbox_manual_tag_editor", replace=True,
        )
        tags_novas_ids = resultado_tags["added_tag_ids"]
        tags_vinculadas = [tags_por_id.get(tag_id) for tag_id in tags_ids_normalizadas if tags_por_id.get(tag_id)]
        if any(bool(getattr(tag, "pausa_permanente", False)) for tag in tags_vinculadas):
            lead.ia_ativa = False
            lead.status_atendimento = "manual"
        # Preserva a lógica de pausa temporária de 24h por tag
        if any(bool(getattr(tag, "acao_transferir_humano", False)) for tag in tags_vinculadas):
            lead.bot_pausado_ate = datetime.utcnow() + timedelta(hours=24)
            lead.status_atendimento = "manual"
        if tags_novas_ids:
            tags_novas_uuid = [uuid.UUID(tag_id) for tag_id in tags_novas_ids]
            result_tags_fechamento = await db.execute(
                select(TagCRM).where(
                    TagCRM.empresa_id == emp_uuid,
                    TagCRM.id.in_(tags_novas_uuid),
                    TagCRM.acao_fechamento == True,
                )
            )
            tags_fechamento = list(result_tags_fechamento.scalars().all())
            if tags_fechamento:
                mover_para_fechado = True
    if data.dados_adicionais is not None:
        lead.dados_adicionais = data.dados_adicionais
    if data.valor_conversao is not None:
        lead.valor_conversao = float(data.valor_conversao)

    if mover_para_fechado:
        result_etapa_fechamento = await db.execute(
            select(CRMEtapa)
            .join(CRMFunil, CRMEtapa.funil_id == CRMFunil.id)
            .where(
                CRMFunil.empresa_id == emp_uuid,
                or_(
                    CRMEtapa.nome.ilike("%fechado%"),
                    CRMEtapa.nome.ilike("%concluído%"),
                    CRMEtapa.nome.ilike("%concluido%")
                )
            )
        )
        etapa_fechado = result_etapa_fechamento.scalars().first()
        if etapa_fechado:
            lead.etapa_id = etapa_fechado.id
            etapa_alterada_no_payload = True

    if etapa_alterada_no_payload:
        # Compatibilidade incremental: quando o CRM legado move etapa,
        # sincroniza a tag de etapa_funil correspondente.
        await sync_tag_etapa_lead(str(lead.id), session=db, preferencia="crm_to_tag")
    elif data.tags is not None:
        # Quando tags são alteradas diretamente, mantém etapa_id legado alinhado.
        await sync_tag_etapa_lead(str(lead.id), session=db, preferencia="tag_to_crm")

    for tag_id in tags_novas_ids:
        await process_business_messaging_conversion_trigger(
            db,
            BusinessMessagingTrigger(
                empresa_id=emp_uuid,
                lead_id=lead.id,
                trigger_type="tag_applied",
                trigger_ref=tag_id,
                source_event_id=f"manual_tag:{lead.id}:{tag_id}",
                trigger_source="manual",
            ),
        )
    if etapa_alterada_no_payload and lead.etapa_id:
        await process_business_messaging_conversion_trigger(
            db,
            BusinessMessagingTrigger(
                empresa_id=emp_uuid,
                lead_id=lead.id,
                trigger_type="crm_stage_changed",
                trigger_ref=str(lead.etapa_id),
                source_event_id=f"manual_stage:{lead.id}:{lead.etapa_id}",
                trigger_source="manual",
            ),
        )

    try:
        await db.commit()
        await db.refresh(lead)
        tags_por_id, tags_por_nome = await _carregar_mapa_tags_empresa(db, emp_uuid)
        from app.services.websocket_manager import manager
        await manager.broadcast_to_empresa(empresa_id, {
            "type": "inbox.lead.tags.updated",
            "tipo_evento": "lead_tags_updated",
            "empresa_id": empresa_id,
            "lead_id": str(lead.id),
            "tags": _montar_tags_front(lead.tags, tags_por_id, tags_por_nome),
        })
        return {
            "id": str(lead.id),
            "nome_contato": lead.nome_contato,
            "telefone_contato": lead.telefone_contato,
            "historico_resumo": lead.historico_resumo,
            "etapa_id": str(lead.etapa_id) if lead.etapa_id else None,
            "status_atendimento": lead.status_atendimento,
            "tags": _montar_tags_front(lead.tags, tags_por_id, tags_por_nome),
            "dados_adicionais": dados_adicionais_for_public_response(lead.dados_adicionais),
            "valor_conversao": lead.valor_conversao,
        }
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao atualizar lead: {str(e)}")


@router.patch("/{empresa_id}/crm/{lead_id}/toggle-ia", status_code=status.HTTP_200_OK)
async def toggle_ia_lead(
    empresa_id: str,
    lead_id: str,
    payload: LeadAIStateUpdate,
    db: AsyncSession = Depends(get_db),
    usuario: Usuario = Depends(require_tenant_access),
):
    if _lead_id_eh_simulador_ou_invalido(lead_id):
        return {"id": lead_id, "ia_ativa": bool(payload.enabled), "manual_enabled": bool(payload.enabled)}

    try:
        emp_uuid = uuid.UUID(empresa_id)
        lead_uuid = uuid.UUID(lead_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(
        select(CRMLead).where(CRMLead.id == lead_uuid, CRMLead.empresa_id == emp_uuid)
    )
    lead = result.scalars().first()
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado")

    manual_state = set_conversation_ai_manual_state(
        lead,
        enabled=bool(payload.enabled),
        actor=str(getattr(usuario, "id", "") or ""),
        reason="inbox_toggle",
    )

    await db.commit()
    await db.refresh(lead)
    logger.info(
        "ai.manual_state.changed empresa_id=%s lead_id=%s enabled=%s actor_type=user",
        empresa_id,
        str(lead.id),
        bool(lead.ia_ativa),
    )

    from app.services.websocket_manager import manager

    await manager.broadcast_to_empresa(
        empresa_id,
        {
            "type": "inbox.ai_state.updated",
            "tipo_evento": "ai_state_updated",
            "lead_id": str(lead.id),
            "telefone": str(lead.telefone_contato or ""),
            "payload": {
                "lead_id": str(lead.id),
                "ia_ativa": bool(lead.ia_ativa),
            },
            "ai_state": {
                **manual_state.as_dict(),
                "state": "active" if lead.ia_ativa else "blocked",
                "reason": "allowed" if lead.ia_ativa else "ai_manual_disabled",
                "reason_code": "allowed" if lead.ia_ativa else "ai_manual_disabled",
                "reason_label": "IA autorizada" if lead.ia_ativa else "IA desligada nesta conversa",
                "label": "IA autorizada" if lead.ia_ativa else "IA desligada nesta conversa",
                "description": (
                    "A IA está pronta para processar novas mensagens durante o teste ativo."
                    if lead.ia_ativa
                    else "Ative o controle de IA para permitir respostas automáticas durante o teste."
                ),
            },
        },
    )

    final_manual_state = get_conversation_manual_ai_state(lead)
    return {
        "id": str(lead.id),
        "ia_ativa": bool(lead.ia_ativa),
        "manual_enabled": final_manual_state.manual_enabled,
        "manual_source": final_manual_state.source,
        "status_atendimento": str(lead.status_atendimento or "aberto"),
    }




@router.post("/{empresa_id}/followups/media", status_code=status.HTTP_201_CREATED)
async def upload_followup_audio(
    empresa_id: str,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        emp_uuid = uuid.UUID(str(empresa_id))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")
    data = await file.read()
    try:
        original_name, mime_type, byte_size = validate_audio_bytes(
            data,
            declared_mime=file.content_type,
            original_name=file.filename,
        )
    except MediaValidationError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    content_hash = hashlib.sha256(data).hexdigest()
    existing = await db.execute(
        select(MediaAsset).where(MediaAsset.empresa_id == emp_uuid, MediaAsset.content_hash == content_hash)
    )
    asset = existing.scalars().first()
    if asset is None:
        asset_id = uuid.uuid4()
        storage_key, _ = persist_audio_bytes(
            empresa_id=str(emp_uuid), asset_id=str(asset_id), original_name=original_name, data=data
        )
        duration_ms = probe_audio_duration_ms(resolve_storage_path(storage_key, empresa_id=str(emp_uuid)))
        asset = MediaAsset(
            id=asset_id,
            empresa_id=emp_uuid,
            media_type="audio",
            original_name=original_name,
            mime_type=mime_type,
            byte_size=byte_size,
            content_hash=content_hash,
            storage_key=storage_key,
            duration_ms=duration_ms,
            status="ready",
        )
        db.add(asset)
        await db.commit()
        await db.refresh(asset)
    return {
        "id": str(asset.id),
        "empresa_id": str(asset.empresa_id),
        "media_type": asset.media_type,
        "original_name": sanitize_original_name(asset.original_name),
        "mime_type": asset.mime_type,
        "byte_size": asset.byte_size,
        "duration_ms": asset.duration_ms,
        "status": asset.status,
    }


@router.get("/{empresa_id}/followups", response_model=List[FollowUpResponse])
async def listar_followups(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        emp_uuid = uuid.UUID(str(empresa_id))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(
        select(ConfigFollowUp)
        .where(ConfigFollowUp.empresa_id == emp_uuid)
        .order_by(ConfigFollowUp.tempo_gatilho_minutos.asc(), ConfigFollowUp.nome.asc())
    )
    items = result.scalars().all()
    followup_ids = [item.id for item in items]
    steps = await _carregar_followup_steps(db, emp_uuid, followup_ids)
    stats = await _followup_execution_stats(db, emp_uuid, followup_ids)
    output = []
    for item in items:
        payload = _serialize_followup_item(item, steps.get(str(item.id)))
        payload.update(stats.get(str(item.id), {}))
        output.append(payload)
    return output


@router.post("/{empresa_id}/followups", response_model=FollowUpResponse, status_code=status.HTTP_201_CREATED)
async def criar_followup(
    empresa_id: str,
    payload: FollowUpCreate,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        emp_uuid = uuid.UUID(str(empresa_id))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    spec = FollowupRuleSpec.from_payload(payload)
    if not str(payload.nome or "").strip():
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="nome é obrigatório")
    await _validate_followup_media(db, emp_uuid, spec)

    item = ConfigFollowUp(
        empresa_id=emp_uuid,
        nome=str(payload.nome or "").strip(),
        tempo_gatilho_minutos=max(1, int(spec.delay_minutes or 0)),
        objetivo_prompt=str(spec.message_content or "").strip(),
        ativo=bool(spec.mode and (payload.ativo if payload.ativo is not None else True)),
    )
    db.add(item)
    await db.flush()
    await _upsert_followup_step(db, emp_uuid, item.id, spec)
    await db.commit()
    await db.refresh(item)
    steps = await _carregar_followup_steps(db, emp_uuid, [item.id])
    return _serialize_followup_item(item, steps.get(str(item.id)))


@router.put("/{empresa_id}/followups/{id}", response_model=FollowUpResponse)
async def atualizar_followup(
    empresa_id: str,
    id: str,
    payload: FollowUpUpdate,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        emp_uuid = uuid.UUID(str(empresa_id))
        followup_uuid = uuid.UUID(str(id))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(
        select(ConfigFollowUp).where(ConfigFollowUp.id == followup_uuid, ConfigFollowUp.empresa_id == emp_uuid)
    )
    item = result.scalars().first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Follow-up não encontrado")

    current_steps = await _carregar_followup_steps(db, emp_uuid, [item.id])
    current_spec = FollowupRuleSpec.from_row(item, current_steps.get(str(item.id)))
    spec = FollowupRuleSpec.from_payload(payload, base=current_spec)
    await _validate_followup_media(db, emp_uuid, spec)

    if payload.nome is not None:
        item.nome = str(payload.nome).strip()
    item.tempo_gatilho_minutos = max(1, int(spec.delay_minutes or 0))
    item.objetivo_prompt = str(spec.message_content or "").strip()
    if payload.ativo is not None:
        item.ativo = bool(payload.ativo)

    await _upsert_followup_step(db, emp_uuid, item.id, spec)
    await db.commit()
    await db.refresh(item)
    steps = await _carregar_followup_steps(db, emp_uuid, [item.id])
    return _serialize_followup_item(item, steps.get(str(item.id)))


@router.delete("/{empresa_id}/followups/{id}", status_code=status.HTTP_200_OK)
async def excluir_followup(
    empresa_id: str,
    id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        emp_uuid = uuid.UUID(str(empresa_id))
        followup_uuid = uuid.UUID(str(id))
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID inválido")

    result = await db.execute(
        select(ConfigFollowUp).where(ConfigFollowUp.id == followup_uuid, ConfigFollowUp.empresa_id == emp_uuid)
    )
    item = result.scalars().first()
    if not item:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Follow-up não encontrado")

    await db.delete(item)
    await db.commit()
    return {"success": True, "id": str(followup_uuid)}


@router.post("/{empresa_id}/leads/{lead_id}/transferir_manual", status_code=status.HTTP_200_OK, dependencies=[Depends(require_tenant_access)])
async def transferir_lead_manual(
    empresa_id: str,
    lead_id: str,
    data: TransferenciaManualRequest,
):
    if _lead_id_eh_simulador_ou_invalido(lead_id):
        return {"success": True, "detail": "Ação simulada"}

    resultado = await executar_transferencia_atendimento(
        empresa_id=empresa_id,
        lead_id=lead_id,
        destino_id=data.destino_id,
        resumo_conversa="Transferência manual realizada pelo atendente no painel",
    )

    if resultado.lower().startswith("erro ao transferir atendimento"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=resultado)

    return {"success": True, "detail": resultado}


@router.post("/{empresa_id}/leads/importar", status_code=status.HTTP_201_CREATED)
async def importar_leads(
    empresa_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    tags_iniciais: str = Form("[]"),
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_tenant_access),
):
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    if not file.filename:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Arquivo inválido")

    try:
        tags_iniciais_lista = normalizar_tags(json.loads(tags_iniciais or "[]"))
        tags_por_id, tags_por_nome = await _carregar_mapa_tags_empresa(db, emp_uuid)
        tags_iniciais_lista = _normalizar_tags_lead_para_ids(tags_iniciais_lista, tags_por_id, tags_por_nome)
        file_bytes = await file.read()
        headers, rows = _load_spreadsheet_rows(file_bytes, file.filename)
        nome_col, telefone_col = _detect_lead_columns(headers)
        if not nome_col or not telefone_col:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Não encontrei as colunas de Nome e Telefone no arquivo enviado.",
            )

        etapa_inicial_id = await _garantir_etapa_inicial_crm(db, emp_uuid)
        inseridos = 0
        atualizados = 0
        conversion_triggers_pending: list[tuple[str, str]] = []

        for row in rows:
            nome = str((row or {}).get(nome_col, "")).strip()
            telefone = str((row or {}).get(telefone_col, "")).strip()
            if not nome and not telefone:
                continue

            lead = None
            if telefone:
                result_lead = await db.execute(
                    select(CRMLead).where(
                        CRMLead.empresa_id == emp_uuid,
                        CRMLead.telefone_contato == telefone,
                    )
                )
                lead = result_lead.scalars().first()

            if lead:
                if nome:
                    lead.nome_contato = nome
                tags_antes = {str(tag).strip() for tag in (lead.tags or []) if str(tag).strip()}
                lead.tags = normalizar_tags((lead.tags or []) + tags_iniciais_lista)
                tags_novas = [str(tag).strip() for tag in (lead.tags or []) if str(tag).strip() not in tags_antes]
                for tag_id in tags_novas:
                    conversion_triggers_pending.append((str(lead.id), tag_id))
                atualizados += 1
            else:
                novo_lead = CRMLead(
                    empresa_id=emp_uuid,
                    etapa_id=etapa_inicial_id,
                    nome_contato=nome or telefone or "Lead importado",
                    telefone_contato=telefone or None,
                    tags=tags_iniciais_lista,
                )
                db.add(novo_lead)
                await db.flush()
                for tag_id in tags_iniciais_lista:
                    conversion_triggers_pending.append((str(novo_lead.id), str(tag_id)))
                inseridos += 1

        for lead_id_trigger, tag_id_trigger in conversion_triggers_pending:
            await process_business_messaging_conversion_trigger(
                db,
                BusinessMessagingTrigger(
                    empresa_id=emp_uuid,
                    lead_id=uuid.UUID(lead_id_trigger),
                    trigger_type="tag_applied",
                    trigger_ref=tag_id_trigger,
                    source_event_id=f"lead_import:{lead_id_trigger}:{tag_id_trigger}",
                    trigger_source="integration",
                ),
            )
        await db.commit()
        return {
            "status": "sucesso",
            "mensagem": "Importação concluída.",
            "colunas_detectadas": headers,
            "tags_iniciais": tags_iniciais_lista,
            "inseridos": inseridos,
            "atualizados": atualizados,
            "total_processado": inseridos + atualizados,
        }
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao importar leads: {str(e)}")


@router.get("/{empresa_id}/exportar-leads", dependencies=[Depends(require_tenant_access)])
async def exportar_leads_csv(empresa_id: str, db: AsyncSession = Depends(get_db)):
    """
    Exporta os leads em CSV (UTF-8-SIG), expandindo dados_adicionais em colunas extras.
    """
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    result = await db.execute(
        select(CRMLead)
        .where(CRMLead.empresa_id == emp_uuid)
        .options(selectinload(CRMLead.etapa))
        .order_by(CRMLead.criado_em.desc())
    )
    leads = result.scalars().all()

    extra_keys: set[str] = set()
    for lead in leads:
        extra_keys.update(dados_adicionais_for_public_response(lead.dados_adicionais).keys())

    base_columns = [
        "id",
        "nome_contato",
        "telefone_contato",
        "historico_resumo",
        "etapa_id",
        "etapa_nome",
        "etapa_tipo",
        "tags",
        "criado_em",
    ]
    fieldnames = base_columns + sorted(extra_keys)

    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fieldnames, extrasaction="ignore")
    writer.writeheader()

    for lead in leads:
        row = {
            "id": str(lead.id),
            "nome_contato": lead.nome_contato,
            "telefone_contato": lead.telefone_contato or "",
            "historico_resumo": lead.historico_resumo or "",
            "etapa_id": str(lead.etapa_id) if lead.etapa_id else "",
            "etapa_nome": lead.etapa.nome if lead.etapa else "",
            "etapa_tipo": lead.etapa.tipo if lead.etapa else "",
            "tags": ", ".join(lead.tags or []),
            "criado_em": lead.criado_em.isoformat() if lead.criado_em else "",
        }
        if isinstance(lead.dados_adicionais, dict):
            public_dados = dados_adicionais_for_public_response(lead.dados_adicionais)
            for key in extra_keys:
                value = public_dados.get(key, "")
                row[key] = "" if value is None else str(value)
        writer.writerow(row)

    csv_bytes = output.getvalue().encode("utf-8-sig")
    output.close()

    return Response(
        content=csv_bytes,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f"attachment; filename=leads_{empresa_id}.csv"},
    )


DIAS_SEMANA_ORDENADOS = ("segunda", "terca", "quarta", "quinta", "sexta", "sabado", "domingo")


class AgendaConfigUpdateRequest(BaseModel):
    agenda_config: Dict[str, Any]


def _parse_horario_hhmm(valor: str, campo: str) -> datetime:
    try:
        return datetime.strptime(str(valor).strip(), "%H:%M")
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Campo '{campo}' deve estar no formato HH:MM."
        ) from exc


def _dias_funcionamento_base() -> Dict[str, Dict[str, Any]]:
    return {
        dia: {"aberto": False, "inicio": None, "fim": None}
        for dia in DIAS_SEMANA_ORDENADOS
    }


def _normalizar_dias_funcionamento(
    dias_brutos: Any,
    horario_inicio_legacy: Any = None,
    horario_fim_legacy: Any = None,
) -> Dict[str, Dict[str, Any]]:
    dias_normalizados = _dias_funcionamento_base()
    mapa_dias_curtos = {
        "seg": "segunda",
        "ter": "terca",
        "qua": "quarta",
        "qui": "quinta",
        "sex": "sexta",
        "sab": "sabado",
        "dom": "domingo",
    }

    # Formato novo: {"segunda": {"aberto": true, "inicio": "08:00", "fim": "18:00"}, ...}
    if isinstance(dias_brutos, dict) and any(
        chave in dias_brutos for chave in DIAS_SEMANA_ORDENADOS
    ):
        for dia in DIAS_SEMANA_ORDENADOS:
            item = dias_brutos.get(dia) or {}
            if not isinstance(item, dict):
                continue
            aberto = bool(item.get("aberto", False))
            if not aberto:
                dias_normalizados[dia] = {"aberto": False, "inicio": None, "fim": None}
                continue
            inicio = item.get("inicio")
            fim = item.get("fim")
            if isinstance(inicio, str) and isinstance(fim, str):
                dias_normalizados[dia] = {"aberto": True, "inicio": inicio.strip(), "fim": fim.strip()}
        return dias_normalizados

    # Compatibilidade: formato curto legado {"seg": {...}, ...}
    if isinstance(dias_brutos, dict) and any(chave in dias_brutos for chave in mapa_dias_curtos):
        for curto, longo in mapa_dias_curtos.items():
            item = dias_brutos.get(curto) or {}
            if not isinstance(item, dict):
                continue
            aberto = bool(item.get("aberto", False))
            if not aberto:
                dias_normalizados[longo] = {"aberto": False, "inicio": None, "fim": None}
                continue
            inicio = item.get("inicio")
            fim = item.get("fim")
            if isinstance(inicio, str) and isinstance(fim, str):
                dias_normalizados[longo] = {"aberto": True, "inicio": inicio.strip(), "fim": fim.strip()}
        return dias_normalizados

    def _format_legacy_time(t: Any, default: str) -> str:
        if not t:
            return default
        if isinstance(t, str):
            return t[:5]
        return t.strftime("%H:%M")

    # Formato legado: {"dias": ["seg", "ter", ...]} ou ["segunda", ...]
    dias_legado = dias_brutos.get("dias", []) if isinstance(dias_brutos, dict) else []
    inicio_legado = _format_legacy_time(horario_inicio_legacy, "08:00")
    fim_legado = _format_legacy_time(horario_fim_legacy, "18:00")
    for dia in dias_legado:
        dia_norm = str(dia).strip().lower()
        dia_norm = mapa_dias_curtos.get(dia_norm, dia_norm)
        if dia_norm in dias_normalizados:
            dias_normalizados[dia_norm] = {
                "aberto": True,
                "inicio": inicio_legado,
                "fim": fim_legado,
            }
    return dias_normalizados


def _validar_e_normalizar_dias_payload(dias_payload: Any) -> Dict[str, Dict[str, Any]]:
    if not isinstance(dias_payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="agenda_config.dias_funcionamento deve ser um objeto com os dias da semana."
        )

    mapa_dias_curtos = {
        "seg": "segunda",
        "ter": "terca",
        "qua": "quarta",
        "qui": "quinta",
        "sex": "sexta",
        "sab": "sabado",
        "dom": "domingo",
    }
    if any(chave in dias_payload for chave in mapa_dias_curtos):
        dias_payload = {
            mapa_dias_curtos.get(chave, chave): valor
            for chave, valor in dias_payload.items()
        }

    chaves_recebidas = set(dias_payload.keys())
    chaves_esperadas = set(DIAS_SEMANA_ORDENADOS)
    if chaves_recebidas != chaves_esperadas:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="agenda_config.dias_funcionamento deve conter exatamente as chaves: segunda, terca, quarta, quinta, sexta, sabado, domingo."
        )

    resultado: Dict[str, Dict[str, Any]] = {}
    for dia in DIAS_SEMANA_ORDENADOS:
        item = dias_payload.get(dia)
        if not isinstance(item, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"agenda_config.dias_funcionamento.{dia} deve ser um objeto."
            )

        aberto = item.get("aberto", None)
        if not isinstance(aberto, bool):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"agenda_config.dias_funcionamento.{dia}.aberto deve ser boolean."
            )

        inicio = item.get("inicio")
        fim = item.get("fim")

        if aberto:
            if not isinstance(inicio, str) or not isinstance(fim, str):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"agenda_config.dias_funcionamento.{dia}.inicio e agenda_config.dias_funcionamento.{dia}.fim devem ser strings HH:MM quando aberto=true."
                )
            dt_inicio = _parse_horario_hhmm(inicio, f"agenda_config.dias_funcionamento.{dia}.inicio")
            dt_fim = _parse_horario_hhmm(fim, f"agenda_config.dias_funcionamento.{dia}.fim")
            if dt_inicio >= dt_fim:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"agenda_config.dias_funcionamento.{dia}.inicio deve ser menor que agenda_config.dias_funcionamento.{dia}.fim."
                )
            resultado[dia] = {"aberto": True, "inicio": dt_inicio.strftime("%H:%M"), "fim": dt_fim.strftime("%H:%M")}
            continue

        # Quando fechado, o padrão aceito é null/null
        if inicio is not None or fim is not None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"agenda_config.dias_funcionamento.{dia}.inicio e agenda_config.dias_funcionamento.{dia}.fim devem ser null quando aberto=false."
            )
        resultado[dia] = {"aberto": False, "inicio": None, "fim": None}

    return resultado


def _validar_e_normalizar_excecoes_payload(excecoes_payload: Any) -> List[Dict[str, Any]]:
    if excecoes_payload is None:
        return []
    if not isinstance(excecoes_payload, list):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="agenda_config.excecoes deve ser uma lista."
        )

    excecoes_normalizadas: List[Dict[str, Any]] = []
    for idx, item in enumerate(excecoes_payload):
        if not isinstance(item, dict):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"agenda_config.excecoes[{idx}] deve ser um objeto."
            )

        data_str = str(item.get("data") or "").strip()
        titulo_str = str(item.get("titulo") or "").strip()
        aberto = item.get("aberto")

        if not data_str:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"agenda_config.excecoes[{idx}].data é obrigatório."
            )
        try:
            datetime.strptime(data_str, "%Y-%m-%d")
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"agenda_config.excecoes[{idx}].data deve estar no formato YYYY-MM-DD."
            ) from exc

        if not titulo_str:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"agenda_config.excecoes[{idx}].titulo é obrigatório."
            )
        if not isinstance(aberto, bool):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"agenda_config.excecoes[{idx}].aberto deve ser boolean."
            )

        inicio = item.get("inicio")
        fim = item.get("fim")
        if aberto:
            if not isinstance(inicio, str) or not isinstance(fim, str):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"agenda_config.excecoes[{idx}].inicio e fim devem ser strings HH:MM quando aberto=true."
                )
            dt_inicio = _parse_horario_hhmm(inicio, f"agenda_config.excecoes[{idx}].inicio")
            dt_fim = _parse_horario_hhmm(fim, f"agenda_config.excecoes[{idx}].fim")
            if dt_inicio >= dt_fim:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"agenda_config.excecoes[{idx}].inicio deve ser menor que fim."
                )
            excecoes_normalizadas.append(
                {
                    "data": data_str,
                    "titulo": titulo_str,
                    "aberto": True,
                    "inicio": dt_inicio.strftime("%H:%M"),
                    "fim": dt_fim.strftime("%H:%M"),
                }
            )
        else:
            if inicio is not None or fim is not None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"agenda_config.excecoes[{idx}].inicio e fim devem ser null quando aberto=false."
                )
            excecoes_normalizadas.append(
                {
                    "data": data_str,
                    "titulo": titulo_str,
                    "aberto": False,
                    "inicio": None,
                    "fim": None,
                }
            )

    return excecoes_normalizadas


def _serialize_agenda_config(config: AgendaConfiguracao | None) -> dict:
    if not config:
        return {
            "dias_funcionamento": _dias_funcionamento_base(),
            "excecoes": [],
            "duracao_minutos": 30
        }
    dias_obj = _normalizar_dias_funcionamento(
        config.dias_funcionamento,
        horario_inicio_legacy=config.horario_inicio,
        horario_fim_legacy=config.horario_fim,
    )
    return {
        "dias_funcionamento": dias_obj,
        "excecoes": config.excecoes if isinstance(config.excecoes, list) else [],
        "duracao_minutos": config.duracao_slot_minutos
    }


@router.put("/{empresa_id}/agenda", status_code=status.HTTP_200_OK, dependencies=[Depends(require_tenant_access)])
async def atualizar_agenda(
    empresa_id: str,
    payload: AgendaConfigUpdateRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Upsert da configuração da agenda por dia da semana.
    A fonte de verdade é o JSONB dias_funcionamento.
    """
    try:
        try:
            emp_uuid = uuid.UUID(empresa_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido") from exc

        agenda_config = payload.agenda_config if isinstance(payload.agenda_config, dict) else {}
        # Compatibilidade: aceita tanto agenda_config direto com dias quanto
        # agenda_config.dias_funcionamento com o mesmo conteúdo.
        dias_payload = agenda_config.get("dias_funcionamento") or agenda_config
        chaves_dia_longas = set(DIAS_SEMANA_ORDENADOS)
        chaves_dia_curtas = {"seg", "ter", "qua", "qui", "sex", "sab", "dom"}
        if (
            not dias_payload
            or not isinstance(dias_payload, dict)
            or not any(chave in dias_payload for chave in (chaves_dia_longas | chaves_dia_curtas))
        ):
            dias_payload = _dias_funcionamento_base()
        dias_normalizados = _validar_e_normalizar_dias_payload(dias_payload)
        excecoes_normalizadas = _validar_e_normalizar_excecoes_payload(agenda_config.get("excecoes"))

        result_config = await db.execute(
            select(AgendaConfiguracao).where(AgendaConfiguracao.empresa_id == emp_uuid)
        )
        config = result_config.scalars().first()

        if config:
            config.dias_funcionamento = dias_normalizados
            config.excecoes = excecoes_normalizadas
            config.horario_inicio = None
            config.horario_fim = None
            if "duracao_minutos" in agenda_config:
                config.duracao_slot_minutos = int(agenda_config["duracao_minutos"])
        else:
            config = AgendaConfiguracao(
                empresa_id=emp_uuid,
                dias_funcionamento=dias_normalizados,
                excecoes=excecoes_normalizadas,
                horario_inicio=None,
                horario_fim=None,
            )
            if "duracao_minutos" in agenda_config:
                config.duracao_slot_minutos = int(agenda_config["duracao_minutos"])
            db.add(config)

        await db.commit()
        await db.refresh(config)

        return _serialize_agenda_config(config)
    except HTTPException:
        await db.rollback()
        raise
    except Exception as e:
        print(f"DEBUG AGENDA ERROR: {str(e)}")
        traceback.print_exc()
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao atualizar configuração da Agenda: {str(e)}"
        )


@router.get("/{empresa_id}/agenda", dependencies=[Depends(require_tenant_access)])
async def obter_agenda(empresa_id: str, db: AsyncSession = Depends(get_db)):
    """
    Retorna as configurações da agenda e a lista de agendamentos futuros da empresa.
    """
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    try:
        # Busca config da agenda
        result_config = await db.execute(
            select(AgendaConfiguracao).where(AgendaConfiguracao.empresa_id == emp_uuid)
        )
        config = result_config.scalars().first()

        # Busca os agendamentos futuros (onde data_hora_inicio >= now, mas aqui para mockup pegaremos todos)
        # e dá load do Lead associado para mostrar o nome
        result_agendamentos = await db.execute(
            select(AgendamentoLocal)
            .where(AgendamentoLocal.empresa_id == emp_uuid)
            .options(selectinload(AgendamentoLocal.lead))
            .order_by(AgendamentoLocal.data_hora_inicio)
        )
        agendamentos_db = result_agendamentos.scalars().all()

        resposta_config = _serialize_agenda_config(config)

        lista_agendamentos = []
        for ag in agendamentos_db:
            lista_agendamentos.append({
                "id": str(ag.id),
                "data_hora_inicio": ag.data_hora_inicio.isoformat() if ag.data_hora_inicio else None,
                "data_hora_fim": ag.data_hora_fim.isoformat() if ag.data_hora_fim else None,
                "status": ag.status,
                "lead": {
                    "nome_contato": ag.lead.nome_contato if ag.lead else "Desconhecido"
                }
            })

        return {
            "config": resposta_config,
            "agendamentos": lista_agendamentos
        }

    except Exception as e:
        print(f"DEBUG AGENDA ERROR: {str(e)}")
        traceback.print_exc()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Erro ao carregar dados da Agenda: {str(e)}"
        )


@router.put("/{empresa_id}/credenciais")
async def atualizar_credenciais(
    empresa_id: str,
    credenciais: EvolutionCredentials,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    """
    Atualiza as credenciais da Evolution API para uma determinada empresa (Super Admin).
    """
    try:
        result = await db.execute(select(Empresa).where(Empresa.id == empresa_id))
        empresa = result.scalars().first()
        
        if not empresa:
            raise HTTPException(status_code=404, detail="Empresa não encontrada")
            
        base = dict(empresa.credenciais_canais or {})
        replacement = str(credenciais.openai_api_key or "").strip()
        if replacement:
            base["openai_api_key"] = replacement
            empresa.openai_api_key = replacement
        empresa.credenciais_canais = base
        
        await db.commit()
        await db.refresh(empresa)
        
        openai_configurada, _ = _empresa_tem_openai_configurada(empresa)
        return {
            "mensagem": "Credenciais atualizadas com sucesso",
            "openai_api_key_configurada": openai_configurada,
        }
        
    except HTTPException as e:
        raise e
    except Exception:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Erro ao atualizar credenciais."
        )


@router.get("/{empresa_id}/openai-models")
async def listar_modelos_openai_empresa(
    empresa_id: str,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    try:
        from app.services.openai_model_catalog import CatalogUnavailable, list_models
        tenant_uuid = uuid.UUID(empresa_id)
        catalog = await list_models(db, tenant_uuid)
        await db.commit()
        # Legacy shape for existing readers; the Super Admin screen uses the
        # richer tenant-scoped /admin/empresas/{id}/openai-models contract.
        return [
            {"id": item["id"], "created": item.get("created") or 0}
            for item in catalog["items"]
            if item.get("compatibility") != "incompatible"
        ]
    except ValueError:
        await db.rollback()
        raise HTTPException(status_code=400, detail="Empresa ou credencial OpenAI inválida.") from None
    except Exception as exc:
        await db.rollback()
        from app.services.openai_model_catalog import CatalogUnavailable
        if isinstance(exc, CatalogUnavailable):
            raise HTTPException(status_code=502, detail="Não foi possível consultar o catálogo OpenAI deste tenant.") from None
        raise HTTPException(status_code=502, detail="Não foi possível consultar o catálogo OpenAI deste tenant.") from None


class SimuladorRequest(BaseModel):
    mensagem: str
    sessao_id: str

@router.post("/{empresa_id}/simulador", status_code=status.HTTP_202_ACCEPTED, dependencies=[Depends(require_tenant_access)])
async def simulador_chat(
    empresa_id: str,
    payload: SimuladorRequest,
    background_tasks: BackgroundTasks
):
    """
    Fire-and-Forget: salva a mensagem no Redis via handle_debouncer em background
    e retorna 202 imediatamente. A resposta da IA fica disponível via GET /simulador/resposta/{sessao_id}.
    """
    from app.api.utils import handle_debouncer
    from app.api.main import redis_client

    # Limpa qualquer resposta anterior desta sessão antes de iniciar novo ciclo
    response_key = simulator_response_key(empresa_id, payload.sessao_id)
    await redis_client.delete(response_key)

    msg = StandardMessage(
        empresa_id=empresa_id,
        canal="simulador",
        identificador_origem=payload.sessao_id,
        texto_mensagem=payload.mensagem,
        is_human_agent=False
    )
    background_tasks.add_task(handle_debouncer, msg)
    return {"status": "received", "sessao_id": payload.sessao_id}


@router.get("/{empresa_id}/simulador/resposta/{sessao_id}", dependencies=[Depends(require_tenant_access)])
async def simulador_poll_resposta(empresa_id: str, sessao_id: str):
    """
    Long-polling: o frontend chama esta rota a cada 2s até receber status='concluido'.
    Quando o fluxo de agentes finaliza, ele grava a resposta no Redis com chave sim_resp:{sessao_id}.
    """
    from app.api.main import redis_client

    resposta_raw = await redis_client.get(simulator_response_key(empresa_id, sessao_id))
    if resposta_raw is None:
        return {"status": "processando", "resposta": None}

    resposta = resposta_raw if isinstance(resposta_raw, str) else resposta_raw.decode("utf-8")
    # Polling is read-only; the producer owns cleanup through key expiration.
    return {"status": "concluido", "resposta": resposta}

@router.delete("/{empresa_id}/simulador/reset", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_tenant_access)])
async def resetar_simulador(empresa_id: str, db: AsyncSession = Depends(get_db)):
    """
    Remove fisicamente o Lead Mock do simulador e todo o seu histórico.
    """
    import uuid
    from db.models import CRMLead
    try:
        emp_uuid = uuid.UUID(empresa_id)
    except ValueError:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    await _limpar_estado_simulador_redis(empresa_id)

    result = await db.execute(select(CRMLead).where(
        CRMLead.empresa_id == emp_uuid,
        CRMLead.telefone_contato == "ID_TESTE_SIMULADOR"
    ))
    lead = result.scalars().first()
    
    if not lead:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
        
    try:
        await db.delete(lead)
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao resetar simulador: {str(e)}")

@router.delete("/{empresa_id}/crm/leads/{lead_id}", status_code=status.HTTP_204_NO_CONTENT, dependencies=[Depends(require_tenant_access)])
async def deletar_lead(
    empresa_id: str,
    lead_id: str,
    conversation_id: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    """
    Remove fisicamente um Lead e todo o seu histórico de mensagens do banco de dados (Hard Delete).
    """
    if _lead_id_eh_simulador_ou_invalido(lead_id):
        await _limpar_estado_simulador_redis(
            empresa_id,
            lead_id if lead_id == SIMULADOR_LEAD_ID else SIMULADOR_LEAD_ID,
        )
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    emp_uuid = _parse_uuid_or_none(empresa_id)
    if not emp_uuid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de empresa inválido")

    l_uuid = _parse_uuid_or_none(lead_id)
    if not l_uuid:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de lead inválido")

    result = await db.execute(select(CRMLead).where(CRMLead.id == l_uuid, CRMLead.empresa_id == emp_uuid))
    lead = result.scalars().first()
    
    if not lead:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lead não encontrado ou não pertence a esta empresa")

    if conversation_id:
        c_uuid = _parse_uuid_or_none(conversation_id)
        if not c_uuid:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="ID de conversa inválido")
        conversation_result = await db.execute(
            select(ChannelConversation).where(
                ChannelConversation.id == c_uuid,
                ChannelConversation.empresa_id == emp_uuid,
                or_(
                    ChannelConversation.lead_id == lead.id,
                    select(MensagemHistorico.id).where(
                        MensagemHistorico.empresa_id == emp_uuid,
                        MensagemHistorico.lead_id == lead.id,
                        MensagemHistorico.channel_conversation_id == ChannelConversation.id,
                    ).exists(),
                ),
            )
        )
        conversation = conversation_result.scalars().first()
        if not conversation:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversa não encontrada ou não pertence a este lead")
        try:
            await db.execute(
                delete(MensagemHistorico).where(
                    MensagemHistorico.empresa_id == emp_uuid,
                    MensagemHistorico.channel_conversation_id == conversation.id,
                )
            )
            await db.execute(
                delete(FollowupExecution).where(
                    FollowupExecution.empresa_id == emp_uuid,
                    FollowupExecution.conversation_id == conversation.id,
                )
            )
            await db.delete(conversation)
            await db.commit()
            return Response(status_code=status.HTTP_204_NO_CONTENT)
        except Exception as e:
            await db.rollback()
            raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao excluir conversa: {str(e)}")
        
    try:
        # 1) Limpa histórico do lead (mensagens_historico) antes de apagar o lead.
        await db.execute(
            delete(MensagemHistorico).where(MensagemHistorico.lead_id == lead.id)
        )
        # Garante execução imediata da limpeza de dependências com FK.
        await db.flush()

        # Dependências adicionais de relacionamento.
        await db.execute(
            delete(HistoricoTransferencia).where(HistoricoTransferencia.lead_id == lead.id)
        )
        # Follow-up executions keep lead_id NOT NULL. Remove only executions
        # owned by this hard-deleted lead before the ORM tries to null them.
        await db.execute(
            delete(FollowupExecution).where(
                FollowupExecution.lead_id == lead.id,
                FollowupExecution.empresa_id == lead.empresa_id,
            )
        )
        await db.execute(
            update(AgendamentoLocal)
            .where(AgendamentoLocal.lead_id == lead.id)
            .values(lead_id=None)
        )
        await db.flush()

        # 2) Remove o lead somente após limpar registros filhos.
        await db.delete(lead)
        # 3) Commit final da transação.
        await db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=f"Erro ao deletar lead: {str(e)}")

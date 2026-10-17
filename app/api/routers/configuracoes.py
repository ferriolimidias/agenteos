from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from db.database import get_db
from db.models import ConfiguracoesGlobais
from app.schemas import ConfiguracaoGlobalResponse, ConfiguracaoGlobalUpdate
from app.api.routers.auth import Usuario, require_super_admin
from app.services.meta.settings import meta_settings

router = APIRouter(prefix="/api/admin", tags=["Configurações Globais"])


public_router = APIRouter(prefix="/api/public", tags=["Configurações Públicas"])


def _config_response(config: ConfiguracoesGlobais) -> dict:
    return {
        "id": config.id,
        "nome_sistema": config.nome_sistema,
        "cor_primaria": config.cor_primaria,
        "openai_key_global_configurada": bool(str(config.openai_key_global or "").strip()),
        "favicon_base64": config.favicon_base64,
        "logo_base64": config.logo_base64,
    }


def _global_integrations_response(config: ConfiguracoesGlobais | None, settings=None) -> dict:
    """Expose platform integration readiness without returning secret values."""
    settings = settings or meta_settings()
    return {
        "meta": {
            "app_id_configured": bool(settings.app_id),
            "app_secret_configured": bool(settings.app_secret),
            "embedded_signup_configured": bool(settings.embedded_signup_config_id),
            "webhook_verification_configured": bool(settings.webhook_verify_token),
            "redirect_uri_configured": bool(settings.redirect_uri),
            "credential_encryption_configured": bool(settings.credential_encryption_key),
            "public_configuration_ready": settings.public_configured,
            "server_configuration_ready": settings.server_configured,
            "graph_api_version": settings.graph_api_version,
            "marketing_graph_api_version": settings.marketing_graph_api_version,
            "live_actions_enabled": settings.live_actions_enabled,
            "marketing_write_enabled": settings.marketing_write_enabled,
            "campaign_create_enabled": settings.marketing_campaign_create_enabled,
            "adset_create_enabled": settings.marketing_adset_create_enabled,
            "creative_create_enabled": settings.marketing_creative_create_enabled,
            "ad_create_enabled": settings.marketing_ad_create_enabled,
            "existing_post_ads_enabled": settings.existing_post_ads_enabled,
        },
        "openai": {
            "global_credential_configured": bool(str(getattr(config, "openai_key_global", None) or "").strip()),
        },
        "marketing_credentials": {"scope": "per_company"},
        "jev": {"scope": "per_company"},
    }


@public_router.get("/configuracoes-branding")
async def get_configuracoes_branding_publico(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(ConfiguracoesGlobais).where(ConfiguracoesGlobais.id == 1))
    config = result.scalars().first()
    if not config:
        return {
            "nome_sistema": "ANTIGRAVITY",
            "logo_base64": None,
            "favicon_base64": None,
        }
    return {
        "nome_sistema": config.nome_sistema or "ANTIGRAVITY",
        "logo_base64": config.logo_base64,
        "favicon_base64": config.favicon_base64,
    }

@router.get("/configuracoes", response_model=ConfiguracaoGlobalResponse)
async def get_configuracoes_globais(
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    result = await db.execute(select(ConfiguracoesGlobais).where(ConfiguracoesGlobais.id == 1))
    config = result.scalars().first()
    
    if not config:
        # GET is a read path. Defaults are represented in the response and are
        # persisted only when an administrator uses the explicit PUT endpoint.
        config = ConfiguracoesGlobais(
            id=1,
            nome_sistema="ANTIGRAVITY",
            cor_primaria="#6366f1",
            openai_key_global=None,
            favicon_base64=None,
            logo_base64=None,
        )
        
    return _config_response(config)


@router.get("/integrations/global-status")
async def get_global_integrations_status(
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    result = await db.execute(select(ConfiguracoesGlobais).where(ConfiguracoesGlobais.id == 1))
    return _global_integrations_response(result.scalars().first())

@router.put("/configuracoes", response_model=ConfiguracaoGlobalResponse)
async def update_configuracoes_globais(
    config_data: ConfiguracaoGlobalUpdate,
    db: AsyncSession = Depends(get_db),
    _: Usuario = Depends(require_super_admin),
):
    result = await db.execute(select(ConfiguracoesGlobais).where(ConfiguracoesGlobais.id == 1))
    config = result.scalars().first()
    
    if not config:
        config = ConfiguracoesGlobais(id=1)
        db.add(config)
    
    config.nome_sistema = config_data.nome_sistema
    config.cor_primaria = config_data.cor_primaria
    if config_data.remover_openai_key_global:
        config.openai_key_global = None
    elif config_data.openai_key_global is not None and str(config_data.openai_key_global).strip():
        config.openai_key_global = str(config_data.openai_key_global).strip()
    config.favicon_base64 = config_data.favicon_base64
    config.logo_base64 = config_data.logo_base64
    await db.commit()
    await db.refresh(config)
    
    return _config_response(config)

@router.get("/modelos-ia", response_model=list[dict[str, str]])
async def get_modelos_disponiveis(_: Usuario = Depends(require_super_admin)):
    from app.api.utils import get_available_models
    return await get_available_models()

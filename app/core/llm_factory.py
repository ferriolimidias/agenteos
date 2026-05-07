import os
import uuid
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from db.models import Empresa
from openai import AuthenticationError, RateLimitError


def normalize_model_name(model_name: str | None) -> str:
    raw = str(model_name or "").strip()
    if not raw:
        return "gpt-4o-mini"

    lowered = raw.lower()

    # Normaliza aliases de UI para o modelo padrão (Standard) sem sufixo "-mini".
    if lowered in {"gpt-5.4", "gpt-5.4-standard", "gpt-5.4 (standard)", "gpt-5.4 standard", "gpt-5.4-v2"}:
        return "gpt-5.4"

    # Evita downgrade implícito para mini quando a intenção explícita é Standard.
    if lowered.startswith("gpt-5.4") and "mini" not in lowered and "nano" not in lowered:
        return "gpt-5.4"

    return raw


def get_llm_model(model_name: str, api_key: str | None = None):
    normalized_model = normalize_model_name(model_name)
    model_lower = normalized_model.lower()

    temperature = 0.7
    if model_lower.startswith("o"):
        temperature = 1.0

    openai_model_kwargs = {"frequency_penalty": 0.4, "presence_penalty": 0.4}

    if model_lower.startswith("gemini-"):
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI
        except ImportError:
            raise ImportError("Instale langchain-google-genai para usar modelos Gemini.")
        key = api_key or os.environ.get("GOOGLE_API_KEY")
        return ChatGoogleGenerativeAI(model=normalized_model, temperature=temperature, google_api_key=key)

    if model_lower.startswith("claude-"):
        try:
            from langchain_anthropic import ChatAnthropic
        except ImportError:
            raise ImportError("Instale langchain-anthropic para usar modelos Claude.")
        key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        return ChatAnthropic(model=normalized_model, temperature=temperature, anthropic_api_key=key)

    if model_lower.startswith("gpt-") or model_lower.startswith("o"):
        from langchain_openai import ChatOpenAI
        key = str(api_key or "").strip()
        if not key:
            raise ValueError("Chave OpenAI ausente para instanciar modelo em modo BYOK.")
        if model_lower.startswith("o"):
            return ChatOpenAI(model=normalized_model, api_key=key, model_kwargs=openai_model_kwargs)
        return ChatOpenAI(
            model=normalized_model,
            temperature=0.7,
            api_key=key,
            model_kwargs=openai_model_kwargs,
        )
    raise ValueError(f"Modelo não suportado pela fábrica atual: {normalized_model}")


async def get_tenant_api_key(empresa_id: str, db: AsyncSession) -> str:
    try:
        empresa_uuid = uuid.UUID(str(empresa_id))
    except ValueError as exc:
        raise ValueError("empresa_id inválido para carregamento de chave OpenAI.") from exc

    result = await db.execute(select(Empresa).where(Empresa.id == empresa_uuid))
    empresa = result.scalars().first()
    if not empresa:
        raise ValueError("Empresa não encontrada.")

    key_direta = str(getattr(empresa, "openai_api_key", "") or "").strip()
    if key_direta:
        return key_direta

    credenciais = getattr(empresa, "credenciais_canais", {}) or {}
    key_credenciais = str(credenciais.get("openai_api_key") or "").strip()
    if key_credenciais:
        return key_credenciais

    raise ValueError("Esta empresa ainda não configurou a chave da OpenAI.")


async def get_llm_for_tenant(
    empresa_id: str,
    db: AsyncSession,
    modelo_escolhido: str | None = None,
):
    tenant_key = await get_tenant_api_key(empresa_id, db)
    modelo_final = normalize_model_name(modelo_escolhido or "gpt-4o-mini")
    return get_llm_model(modelo_final, api_key=tenant_key)


async def get_embeddings_for_tenant(empresa_id: str, db: AsyncSession):
    from langchain_openai import OpenAIEmbeddings

    tenant_key = await get_tenant_api_key(empresa_id, db)
    return OpenAIEmbeddings(model="text-embedding-3-small", api_key=tenant_key)


async def handle_openai_runtime_exception(empresa_id: str, error: Exception) -> None:
    from db.database import AsyncSessionLocal
    from app.services.channel_factory import despachar_mensagem

    if not empresa_id:
        return

    novo_status = None
    enviar_alerta = False
    if isinstance(error, AuthenticationError):
        novo_status = "chave_invalida"
    elif isinstance(error, RateLimitError) and "insufficient_quota" in str(error).lower():
        novo_status = "sem_credito"
        enviar_alerta = True
    else:
        return

    try:
        empresa_uuid = uuid.UUID(str(empresa_id))
    except ValueError:
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Empresa).where(Empresa.id == empresa_uuid))
        empresa = result.scalars().first()
        if not empresa:
            return

        empresa.status_openai = novo_status
        telefone_notificacao = str(getattr(empresa, "telefone_notificacao", "") or "").strip()
        await session.commit()

    if enviar_alerta and telefone_notificacao:
        try:
            await despachar_mensagem(
                canal="evolution",
                identificador_origem=telefone_notificacao,
                texto="⚠️ Seu AgenteOS foi pausado. Seus créditos da OpenAI esgotaram. Recarregue sua conta para que a IA volte a responder.",
                empresa_id=str(empresa_id),
            )
        except Exception:
            pass


async def mark_openai_status_ok(empresa_id: str) -> None:
    from db.database import AsyncSessionLocal

    if not empresa_id:
        return
    try:
        empresa_uuid = uuid.UUID(str(empresa_id))
    except ValueError:
        return

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(Empresa).where(Empresa.id == empresa_uuid))
        empresa = result.scalars().first()
        if not empresa:
            return
        if str(getattr(empresa, "status_openai", "ok") or "ok") == "ok":
            return
        empresa.status_openai = "ok"
        await session.commit()

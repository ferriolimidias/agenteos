import os


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
        key = api_key or os.environ.get("OPENAI_API_KEY")
        if model_lower.startswith("o"):
            if key:
                return ChatOpenAI(model=normalized_model, api_key=key, model_kwargs=openai_model_kwargs)
            return ChatOpenAI(model=normalized_model, model_kwargs=openai_model_kwargs)
        if key:
            return ChatOpenAI(
                model=normalized_model,
                temperature=temperature,
                api_key=key,
                model_kwargs=openai_model_kwargs,
            )
        return ChatOpenAI(
            model=normalized_model,
            temperature=temperature,
            model_kwargs=openai_model_kwargs,
        )

    from langchain_openai import ChatOpenAI
    key = api_key or os.environ.get("OPENAI_API_KEY")
    if key:
        return ChatOpenAI(
            model="gpt-4o-mini",
            temperature=0.7,
            api_key=key,
            model_kwargs=openai_model_kwargs,
        )
    return ChatOpenAI(
        model="gpt-4o-mini",
        temperature=0.7,
        model_kwargs=openai_model_kwargs,
    )

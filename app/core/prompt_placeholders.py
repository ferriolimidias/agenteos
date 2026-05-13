"""Substituição determinística de placeholders em prompts (sem depender do LLM)."""


def substituir_placeholders_nome_lead_em_texto(texto: str, nome_lead: str) -> str:
    """
    Injeta o nome do lead no texto antes do envio ao modelo.
    Painéis usam `{nome_lead}`; `[Nome]` permanece por compatibilidade com copy legada.
    """
    if texto is None:
        return ""
    n = (nome_lead or "").strip()
    s = str(texto)
    s = s.replace("{nome_lead}", n)
    s = s.replace("[Nome]", n)
    return s

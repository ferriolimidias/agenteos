from __future__ import annotations

import re
import sys
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.tokenized_links import (  # noqa: E402
    generate_or_get_tokenized_link,
    mask_tokenized_url_for_logs,
    render_tokenized_link_message,
    validate_tokenized_link,
)
from db.models import CRMLead, Empresa  # noqa: E402
from scripts.setup_leonardo_metodo_hibrido_leoft import merge_link_tokenizado_config  # noqa: E402


GENERIC_SLUG = "diagnostico_teste"


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[OK] {message}")


def main() -> int:
    empresa_id = uuid.uuid4()
    lead_id = uuid.uuid4()
    empresa = Empresa(
        id=empresa_id,
        nome_empresa="Tenant Genérico",
        credenciais_canais={
            "links_tokenizados": {
                GENERIC_SLUG: {
                    "ativo": True,
                    "slug": GENERIC_SLUG,
                    "base_url": "https://forms.example.invalid/diagnostico?origem=whatsapp",
                    "parametro_token": "lead_token",
                    "validade_horas": 24,
                }
            }
        },
    )
    lead = CRMLead(
        id=lead_id,
        empresa_id=empresa_id,
        nome_contato="Cliente Teste",
        telefone_contato="+55 54 99999-1234",
        dados_adicionais={},
        tags=[],
    )

    first = generate_or_get_tokenized_link(None, empresa, lead, GENERIC_SLUG, sequence_slug="fluxo_generico")
    assert_true(first.url is not None and first.token is not None, "gera URL e token para slug genérico")
    assert_true(len(first.token or "") >= 32, "token tem entropia suficiente")
    assert_true("99999" not in first.url and "Cliente" not in first.url and str(lead_id) not in first.url, "URL não expõe telefone, nome ou lead_id cru")

    parsed = urlsplit(first.url or "")
    query = parse_qs(parsed.query)
    assert_true(query.get("lead_token") == [first.token], "URL usa parâmetro de token configurado")
    assert_true(parsed.netloc == "forms.example.invalid", "URL usa base_url configurada")

    anamnese_empresa = Empresa(
        id=uuid.uuid4(),
        nome_empresa="Tenant Anamnese Genérico",
        credenciais_canais={
            "links_tokenizados": {
                "anamnese_demo": {
                    "ativo": True,
                    "slug": "anamnese_demo",
                    "public_slug": "demo",
                    "base_url": "https://agentos.example.invalid/anamnese/demo",
                    "parametro_token": "token",
                    "validade_horas": 24,
                    "finalidade": "anamnese",
                }
            }
        },
    )
    anamnese_lead = CRMLead(
        id=uuid.uuid4(),
        empresa_id=anamnese_empresa.id,
        nome_contato="Lead Demo",
        telefone_contato="+55 54 98888-0000",
        dados_adicionais={},
        tags=[],
    )
    anamnese_link = generate_or_get_tokenized_link(None, anamnese_empresa, anamnese_lead, "anamnese_demo")
    anamnese_query = parse_qs(urlsplit(anamnese_link.url or "").query)
    assert_true(anamnese_query.get("token") == [anamnese_link.token], "link tokenizado da anamnese usa parâmetro token")

    stored = (lead.dados_adicionais or {}).get("links_tokenizados", {}).get(GENERIC_SLUG, {})
    assert_true(stored.get("token") == first.token and stored.get("token_hash"), "registro fica em dados_adicionais com token_hash")
    assert_true(stored.get("sequencia_slug") == "fluxo_generico", "registro guarda sequence_slug genérico")

    second = generate_or_get_tokenized_link(None, empresa, lead, GENERIC_SLUG, sequence_slug="fluxo_generico")
    assert_true(second.reused is True and second.token == first.token and second.url == first.url, "token é idempotente por lead + slug")

    message = render_tokenized_link_message("Olá, [Nome]\n\nAcesse:\n[LINK]", lead, first)
    assert_true("Cliente Teste" in message and first.url in message, "template substitui nome e link")

    masked = mask_tokenized_url_for_logs(first.url)
    assert_true(first.token not in masked and ("lead_token=%2A%2A%2A" in masked or "lead_token=***" in masked), "máscara remove token de logs")

    missing = generate_or_get_tokenized_link(None, empresa, lead, "slug_inexistente", sequence_slug="fluxo_generico")
    assert_true(missing.operational_pending is True and missing.url is None, "config ausente vira pendência operacional")

    empresa.credenciais_canais["links_tokenizados"][GENERIC_SLUG]["base_url"] = None
    lead.dados_adicionais = {}
    no_base = generate_or_get_tokenized_link(None, empresa, lead, GENERIC_SLUG, sequence_slug="fluxo_generico")
    assert_true(no_base.operational_pending is True and no_base.url is None, "base_url null não inventa link")

    merged = merge_link_tokenizado_config(
        {
            "base_url": "https://forms.example.invalid/anamnese-real",
            "parametro_token": "lead_token",
            "validade_horas": 72,
        }
    )
    assert_true(merged.get("base_url") == "https://forms.example.invalid/anamnese-real", "merge de setup preserva base_url existente")
    assert_true(merged.get("parametro_token") == "lead_token" and merged.get("validade_horas") == 72, "merge de setup preserva campos operacionais")

    source = Path("app/services/tokenized_links.py").read_text(encoding="utf-8")
    for forbidden in ("LeoFT", "Leonardo", "anamnese_leoft", "QUERO COMEÇAR"):
        assert_true(forbidden not in source, f"serviço genérico não contém {forbidden!r}")
    assert_true("validate_tokenized_link" in source, "serviço genérico expõe validação para backend público")
    public_router = Path("app/api/routers/public_anamnese.py").read_text(encoding="utf-8")
    assert_true("lead_id" not in public_router and "telefone_contato" not in public_router.split("return", 1)[-1], "backend público não retorna dados sensíveis crus")

    print("verificar_tokenized_links_generico: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

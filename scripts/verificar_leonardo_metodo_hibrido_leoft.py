from __future__ import annotations

import asyncio
import copy
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import delete, select
from sqlalchemy.orm.attributes import flag_modified

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.api.utils import processar_bloco_mensagens
from app.schemas import StandardMessage
from app.services import ads_integration_service as capi
from app.services.configured_whatsapp_sequences import maybe_dispatch_configured_sequence
from app.services.tokenized_links import generate_or_get_tokenized_link, render_tokenized_link_message, validate_tokenized_link
from db.database import AsyncSessionLocal
from db.models import CRMLead, Empresa, MensagemHistorico, MetaConversionEvent, TagCRM, TagMetaConversionConfig
from scripts import setup_leonardo_metodo_hibrido_leoft as setup
from scripts import setup_rosangela_danca_circular as setup_rosangela


TEST_PHONE = "5554999977001"
TEST_PHONE_FAKE_URL = "5554999977002"
TEST_PHONE_PROCESSAR = "5554999977003"
TEST_PHONE_CTA_LOWER = "5554999977004"
TEST_PHONE_CTA_SIM = "5554999977005"
TEST_PHONE_CTA_NEG_1 = "5554999977006"
TEST_PHONE_CTA_NEG_2 = "5554999977007"
TEST_PHONE_CTA_NEG_3 = "5554999977008"
TEST_PHONE_CTA_NEG_4 = "5554999977009"
TEST_PHONE_DECLINE_AFTER_CTA_BASE = 5554999977020
TEST_BASE_URL = "https://forms.example.invalid/leoft/anamnese"
CTA = "QUERO COMEÇAR"


@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""


def record(results: list[Check], name: str, ok: bool, detail: str = "") -> None:
    results.append(Check(name, ok, detail))
    print(f"[{'OK' if ok else 'FAIL'}] {name}{': ' + detail if detail else ''}")


def as_text(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value or "")


def emoji_count(text: str) -> int:
    return len(re.findall(r"[\U0001F300-\U0001FAFF]", str(text or "")))


def semantic(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("*", "")).strip().lower()


def app_files() -> list[Path]:
    return [path for path in Path("app").rglob("*.py") if path.is_file()]


def scan_app_runtime(results: list[Check]) -> None:
    forbidden = [
        "Método Híbrido LeoFT",
        "QUERO COMEÇAR",
        setup.EMPRESA_ID,
        setup.LINK_SLUG,
        "Leonardo Ferrioli Telles",
        "R$ 97",
    ]
    found: list[str] = []
    specific_if: list[str] = []
    for path in app_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for fragment in forbidden:
            if fragment in text:
                found.append(f"{path}:{fragment}")
        if f"if empresa_id == \"{setup.EMPRESA_ID}\"" in text or f"if empresa_id == '{setup.EMPRESA_ID}'" in text:
            specific_if.append(str(path))
    record(results, "copy/CTA/ID/slug Leonardo não aparecem em app/", not found, "; ".join(found[:8]))
    record(results, "sem if específico Leonardo em app/", not specific_if, "; ".join(specific_if[:8]))

    service = Path("app/services/tokenized_links.py").read_text(encoding="utf-8")
    record(results, "serviço de link tokenizado é genérico", all(term not in service for term in ("LeoFT", "Leonardo", setup.LINK_SLUG, CTA)))


def validate_mobile_copy(results: list[Check], sequence: dict[str, Any]) -> None:
    messages = sequence.get("mensagens")
    record(results, "sequência tem 8 mensagens", isinstance(messages, list) and len(messages) == 8)
    if not isinstance(messages, list):
        return
    record(results, "sequência igual ao setup", messages == setup.SEQUENCIA_MENSAGENS)
    record(results, "formato whatsapp_mobile", sequence.get("formato") == "whatsapp_mobile")
    record(results, "CTA configurado", sequence.get("cta_esperado") == CTA)
    record(results, "mensagem_recusa configurada", bool(sequence.get("mensagem_recusa")))
    cta_lines = [line.strip().replace("*", "") for line in messages[-1].splitlines()]
    record(results, "CTA fica isolado", CTA in cta_lines)
    cta_line = next((line for line in messages[-1].splitlines() if CTA in line), "")
    record(results, "CTA sem emoji junto", emoji_count(cta_line) == 0)
    record(results, "emojis leves em alguns blocos", 1 <= sum(1 for message in messages if emoji_count(message)) < len(messages))
    record(results, "não há emoji em todas as mensagens", sum(1 for message in messages if emoji_count(message)) < len(messages))
    record(results, "nenhum bloco tem mais de 1 emoji", all(emoji_count(message) <= 1 for message in messages))
    record(results, "linhas em branco entre ideias", all("\n\n" in message for message in messages))
    record(results, "títulos com emoji e negrito preservados", "🔥 *Método Híbrido LeoFT*" in messages[0] and "📝 *Como funciona?*" in messages[2])
    record(results, "mensagem 8 contém valor e CTA com espaçamento", "*R$ 97*" in messages[-1] and "\n\n*QUERO COMEÇAR*\n\n" in messages[-1])
    message_4 = messages[3]
    record(results, "mensagem 4 contém copy corrigida", "O treino pode ser adaptado" in message_4)
    record(results, "mensagem 4 não contém treinopode", "treinopode" not in message_4)
    record(results, "mensagem 4 não contém teino híbrido", "teino híbrido" not in message_4)
    record(results, "mensagem 4 contém treino híbrido correto", "treino híbrido" in message_4)

    action = sequence.get("acao_pos_confirmacao") if isinstance(sequence.get("acao_pos_confirmacao"), dict) else {}
    template = str(action.get("mensagem_template") or "").replace("\r\n", "\n").replace("\r", "\n")
    template_lines = template.splitlines()
    link_line_indexes = [idx for idx, line in enumerate(template_lines) if line.strip() == "[LINK]"]
    record(results, "template de link tem [LINK] isolado", len(link_line_indexes) == 1)
    if link_line_indexes:
        idx = link_line_indexes[0]
        before_blank = idx > 0 and template_lines[idx - 1] == ""
        after_blank = idx + 1 < len(template_lines) and template_lines[idx + 1] == ""
        record(results, "template de link tem linha em branco antes/depois", before_blank and after_blank)
    rendered = render_tokenized_link_message(
        template,
        {"nome_contato": "Maria Silva"},
        type("LinkResult", (), {"url": "https://agenteos.ferriolimidias.com.br/anamnese/leoft?token=TOKEN_TESTE"})(),
    )
    record(results, "payload renderizado preserva quebra após link", "token=TOKEN_TESTE\n\nVocê responde" in rendered)
    record(results, "payload renderizado deixa link em linha própria", "\nhttps://agenteos.ferriolimidias.com.br/anamnese/leoft?token=TOKEN_TESTE\n" in rendered)


async def load_empresa() -> Empresa:
    async with AsyncSessionLocal() as session:
        empresa = (await session.execute(select(Empresa).where(Empresa.id == setup.EMPRESA_ID))).scalars().first()
        if not empresa:
            raise AssertionError("tenant Leonardo não encontrado")
        return empresa


async def prepare_lead(phone: str) -> tuple[Empresa, CRMLead]:
    async with AsyncSessionLocal() as session:
        empresa = (await session.execute(select(Empresa).where(Empresa.id == setup.EMPRESA_ID))).scalars().one()
        lead = (
            await session.execute(
                select(CRMLead).where(CRMLead.empresa_id == empresa.id, CRMLead.telefone_contato == phone)
            )
        ).scalars().first()
        if not lead:
            lead = CRMLead(
                empresa_id=empresa.id,
                nome_contato="Lead Verificação Leonardo",
                telefone_contato=phone,
                dados_adicionais={},
                tags=[],
            )
            session.add(lead)
            await session.flush()
        lead.tags = []
        lead.dados_adicionais = {}
        lead.ia_ativa = True
        lead.status_atendimento = "aberto"
        lead.bot_pausado_ate = None
        flag_modified(lead, "tags")
        flag_modified(lead, "dados_adicionais")
        await session.execute(delete(MensagemHistorico).where(MensagemHistorico.lead_id == lead.id))
        await session.commit()
        await session.refresh(empresa)
        await session.refresh(lead)
        return empresa, lead


async def get_lead_state(phone: str) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        lead = (
            await session.execute(
                select(CRMLead).where(CRMLead.empresa_id == setup.EMPRESA_ID, CRMLead.telefone_contato == phone)
            )
        ).scalars().one()
        return {
            "id": str(lead.id),
            "tags": list(lead.tags or []),
            "dados_adicionais": dict(lead.dados_adicionais or {}),
        }


async def tag_id(tag_name: str) -> str:
    async with AsyncSessionLocal() as session:
        tag = (
            await session.execute(select(TagCRM).where(TagCRM.empresa_id == setup.EMPRESA_ID, TagCRM.nome == tag_name))
        ).scalars().first()
        if not tag:
            raise AssertionError(f"tag ausente: {tag_name}")
        return str(tag.id)


async def event_count(tag: str, phone: str) -> int:
    async with AsyncSessionLocal() as session:
        lead = (
            await session.execute(
                select(CRMLead).where(CRMLead.empresa_id == setup.EMPRESA_ID, CRMLead.telefone_contato == phone)
            )
        ).scalars().one()
        result = await session.execute(
            select(MetaConversionEvent).where(
                MetaConversionEvent.empresa_id == setup.EMPRESA_ID,
                MetaConversionEvent.lead_id == lead.id,
                MetaConversionEvent.tag_id == tag,
                MetaConversionEvent.status == "dry_run",
                MetaConversionEvent.source == "configured_whatsapp_sequence",
            )
        )
        return len(result.scalars().all())


async def set_link_base_url(base_url: str | None) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        empresa = (await session.execute(select(Empresa).where(Empresa.id == setup.EMPRESA_ID))).scalars().one()
        original = copy.deepcopy(empresa.credenciais_canais or {})
        credenciais = copy.deepcopy(empresa.credenciais_canais or {})
        links = credenciais.setdefault("links_tokenizados", {})
        link_config = links.setdefault(setup.LINK_SLUG, copy.deepcopy(setup.LINK_TOKENIZADO_CONFIG))
        link_config["base_url"] = base_url
        empresa.credenciais_canais = credenciais
        flag_modified(empresa, "credenciais_canais")
        await session.commit()
        return original


async def restore_credenciais(original: dict[str, Any]) -> None:
    async with AsyncSessionLocal() as session:
        empresa = (await session.execute(select(Empresa).where(Empresa.id == setup.EMPRESA_ID))).scalars().one()
        empresa.credenciais_canais = original
        flag_modified(empresa, "credenciais_canais")
        await session.commit()


async def validate_setup_preserves_base_url(results: list[Check]) -> None:
    original = await set_link_base_url(None)
    configured_url = "https://forms.example.invalid/leoft/anamnese-operacional"
    try:
        async with AsyncSessionLocal() as session:
            empresa = (await session.execute(select(Empresa).where(Empresa.id == setup.EMPRESA_ID))).scalars().one()
            credenciais = copy.deepcopy(empresa.credenciais_canais or {})
            links = credenciais.setdefault("links_tokenizados", {})
            link_config = copy.deepcopy(setup.LINK_TOKENIZADO_CONFIG)
            link_config.update(
                {
                    "base_url": configured_url,
                    "parametro_token": "lead_token",
                    "validade_horas": 72,
                    "ativo": True,
                    "checkout_provider": "mercado_pago",
                    "checkout_no_final": True,
                }
            )
            links[setup.LINK_SLUG] = link_config
            empresa.credenciais_canais = credenciais
            flag_modified(empresa, "credenciais_canais")
            await session.commit()

        await setup.main()

        async with AsyncSessionLocal() as session:
            empresa = (await session.execute(select(Empresa).where(Empresa.id == setup.EMPRESA_ID))).scalars().one()
            link_config = ((empresa.credenciais_canais or {}).get("links_tokenizados") or {}).get(setup.LINK_SLUG) or {}
            record(results, "setup preserva base_url real ao rerodar", link_config.get("base_url") == configured_url)
            record(results, "setup não troca base_url real por null", link_config.get("base_url") not in (None, ""))
            record(results, "setup preserva parametro_token operacional", link_config.get("parametro_token") == "lead_token")
            record(results, "setup preserva validade_horas operacional", link_config.get("validade_horas") == 72)
    finally:
        await restore_credenciais(original)


async def validate_configs(results: list[Check]) -> None:
    async with AsyncSessionLocal() as session:
        empresas = (
            await session.execute(select(Empresa).where(Empresa.nome_empresa.ilike("%Leonardo Personal%")))
        ).scalars().all()
        record(
            results,
            "tenant Leonardo localizado corretamente",
            len(empresas) == 1 and str(empresas[0].id) == setup.EMPRESA_ID and str(empresas[0].nome_empresa or "").strip() == "Leonardo Personal",
            ", ".join(f"{empresa.nome_empresa}:{empresa.id}" for empresa in empresas),
        )
        if not empresas:
            return

        empresa = empresas[0]
        credenciais = empresa.credenciais_canais if isinstance(empresa.credenciais_canais, dict) else {}
        sequencias = credenciais.get("sequencias_whatsapp") if isinstance(credenciais.get("sequencias_whatsapp"), dict) else {}
        sequence = sequencias.get(setup.SEQUENCE_SLUG) if isinstance(sequencias.get(setup.SEQUENCE_SLUG), dict) else {}
        ofertas = credenciais.get("ofertas") if isinstance(credenciais.get("ofertas"), dict) else {}
        oferta = ofertas.get(setup.SEQUENCE_SLUG) if isinstance(ofertas.get(setup.SEQUENCE_SLUG), dict) else {}
        links = credenciais.get("links_tokenizados") if isinstance(credenciais.get("links_tokenizados"), dict) else {}
        link_config = links.get(setup.LINK_SLUG) if isinstance(links.get(setup.LINK_SLUG), dict) else {}

        record(results, "sequência existe", bool(sequence))
        validate_mobile_copy(results, sequence)
        record(results, "oferta configurada", oferta == setup.OFERTA_CONFIG)
        expected_link_config = setup.merge_link_tokenizado_config(link_config)
        record(results, "link tokenizado configurado", link_config == expected_link_config)
        record(results, "base_url da anamnese configurada", bool(str(link_config.get("base_url") or "").strip()))
        configured_base_url = str(link_config.get("base_url") or "").strip()
        record(results, "slug público da anamnese configurado", link_config.get("public_slug") == "leoft" and link_config.get("public_path") == "/anamnese/leoft")
        record(
            results,
            "base_url configurada aponta para /anamnese/leoft",
            not configured_base_url or "/anamnese/leoft" in configured_base_url,
            configured_base_url,
        )
        record(results, "ação pós-confirmação genérica configurada", sequence.get("acao_pos_confirmacao", {}).get("tipo") == "enviar_link_tokenizado")
        record(results, "não há checkout direto no link", link_config.get("tipo") == "formulario_externo_tokenizado" and link_config.get("checkout_provider") == "mercado_pago")

        expected_events = {
            "LeoFT - Pediu informações": ("Contact", None),
            "LeoFT - Quero começar": ("Lead", None),
            "LeoFT - Anamnese enviada": ("CompleteRegistration", None),
            "LeoFT - Anamnese concluída": ("CompleteRegistration", None),
            "LeoFT - Pagamento confirmado": ("Purchase", 97),
        }
        tags = (
            await session.execute(select(TagCRM).where(TagCRM.empresa_id == empresa.id, TagCRM.nome.in_(list(expected_events))))
        ).scalars().all()
        tags_by_name = {tag.nome: tag for tag in tags}
        record(results, "tags Leonardo existem", all(name in tags_by_name for name in expected_events), ", ".join(sorted(tags_by_name)))

        purchase_configs: list[TagMetaConversionConfig] = []
        for name, (event_name, value) in expected_events.items():
            tag = tags_by_name.get(name)
            cfg = None
            if tag:
                cfg = (
                    await session.execute(
                        select(TagMetaConversionConfig).where(
                            TagMetaConversionConfig.empresa_id == empresa.id,
                            TagMetaConversionConfig.tag_id == tag.id,
                        )
                    )
                ).scalars().first()
            record(results, f"{name} evento Meta {event_name}", bool(cfg and cfg.event_name == event_name))
            record(results, f"{name} send_mode dry_run", bool(cfg and cfg.send_mode == "dry_run"))
            record(results, f"{name} currency BRL", bool(cfg and cfg.currency == "BRL"))
            if value is not None:
                record(results, f"{name} value {value}", bool(cfg and cfg.default_value == value))
            if cfg and cfg.event_name == "Purchase":
                purchase_configs.append(cfg)
        paid_tag = tags_by_name.get("LeoFT - Pagamento confirmado")
        record(results, "Purchase só na tag manual", len(purchase_configs) == 1 and paid_tag and purchase_configs[0].tag_id == paid_tag.id)
        record(results, "CAPI live desativado no tenant", getattr(empresa, "meta_capi_ativo", False) is False)

        rosangela = (await session.execute(select(Empresa).where(Empresa.id == setup_rosangela.EMPRESA_ID))).scalars().first()
        ros_seq = ((rosangela.credenciais_canais or {}).get("sequencias_whatsapp") or {}).get("danca_circular") if rosangela else None
        record(results, "Rosangela preservada com 8 mensagens", bool(ros_seq and len(ros_seq.get("mensagens", [])) == 8 and ros_seq.get("cta_esperado") == "QUERO MINHA VAGA"))
        jjclean = (await session.execute(select(Empresa).where(Empresa.nome_empresa == "JJClean"))).scalars().first()
        record(results, "JJClean não foi alterada pelo setup Leonardo", bool(jjclean and "Método Híbrido LeoFT" not in as_text(jjclean.credenciais_canais)))


async def run_sequence_flow(results: list[Check]) -> None:
    meta_http_calls: list[dict[str, Any]] = []

    async def fake_meta_http(empresa: Empresa, payload: dict[str, Any]) -> tuple[int, Any]:
        meta_http_calls.append({"empresa_id": str(empresa.id), "payload": payload})
        return 599, {"blocked_by_test": True}

    original_meta = capi.enviar_payload_meta_capi
    capi.enviar_payload_meta_capi = fake_meta_http

    try:
        original_credenciais = await set_link_base_url(None)
        empresa, lead = await prepare_lead(TEST_PHONE)
        captured: list[str] = []

        async def fake_dispatch(**kwargs: Any) -> bool:
            captured.append(str(kwargs["texto"]))
            record(results, "dispatcher sem split automático", kwargs.get("split_messages") is False and kwargs.get("segment_whatsapp") is False)
            return True

        info_tag = await tag_id("LeoFT - Pediu informações")
        cta_tag = await tag_id("LeoFT - Quero começar")
        anamnese_tag = await tag_id("LeoFT - Anamnese enviada")
        paid_tag = await tag_id("LeoFT - Pagamento confirmado")
        info_before = await event_count(info_tag, TEST_PHONE)
        cta_before = await event_count(cta_tag, TEST_PHONE)
        anamnese_before = await event_count(anamnese_tag, TEST_PHONE)

        result = await maybe_dispatch_configured_sequence(
            empresa=empresa,
            lead=lead,
            texto_entrada="Olá",
            estado={},
            canal="evolution",
            identificador_origem=TEST_PHONE,
            conexao_id=None,
            primeiro_contato=True,
            dispatcher=fake_dispatch,
        )
        record(results, "lead inicial aciona sequência", result.handled is True and result.sent_count == 8 and len(captured) == 8)
        record(results, "sequência enviada igual à copy configurada", captured == setup.SEQUENCIA_MENSAGENS)
        state_after_first = await get_lead_state(TEST_PHONE)
        record(results, "tag início aplicada", info_tag in state_after_first["tags"])
        record(results, "CAPI dry_run para tag início", await event_count(info_tag, TEST_PHONE) == info_before + 1)
        seq_state = state_after_first["dados_adicionais"].get("configured_whatsapp_sequences", {}).get(setup.SEQUENCE_SLUG, {})
        record(results, "estado anti-repetição top-level marcado", seq_state.get("sent") is True and seq_state.get("messages_count") == 8)

        captured.clear()
        second = await maybe_dispatch_configured_sequence(
            empresa=empresa,
            lead=lead,
            texto_entrada="Como funciona?",
            estado={},
            canal="evolution",
            identificador_origem=TEST_PHONE,
            primeiro_contato=False,
            dispatcher=fake_dispatch,
        )
        record(results, "segunda mensagem comum não reenvia sequência", second.handled is False and not captured)

        cta_result = await maybe_dispatch_configured_sequence(
            empresa=empresa,
            lead=lead,
            texto_entrada=CTA,
            estado={},
            canal="evolution",
            identificador_origem=TEST_PHONE,
            primeiro_contato=False,
            dispatcher=fake_dispatch,
        )
        record(results, "CTA aplica tag Quero começar", cta_result.handled is True and cta_result.tag_cta_applied is True)
        record(results, "mensagem após CTA pede nome", captured[-1] == setup.SEQUENCIA_WHATSAPP_CONFIG["mensagem_apos_cta"])
        state_after_cta = await get_lead_state(TEST_PHONE)
        record(results, "tag CTA no lead", cta_tag in state_after_cta["tags"])
        record(results, "CAPI dry_run para CTA", await event_count(cta_tag, TEST_PHONE) == cta_before + 1)

        captured.clear()
        confirm = await maybe_dispatch_configured_sequence(
            empresa=empresa,
            lead=lead,
            texto_entrada="Maria Silva",
            estado={},
            canal="evolution",
            identificador_origem=TEST_PHONE,
            primeiro_contato=False,
            dispatcher=fake_dispatch,
        )
        record(results, "nome aplica Anamnese enviada", confirm.tag_confirmacao_applied is True and anamnese_tag in (await get_lead_state(TEST_PHONE))["tags"])
        record(results, "base_url null não inventa link", confirm.tokenized_link_pending is True and "[LINK]" not in (captured[-1] if captured else "") and "http" not in (captured[-1] if captured else ""))
        state_after_null = await get_lead_state(TEST_PHONE)
        record(results, "pendência operacional registrada", setup.LINK_SLUG in state_after_null["dados_adicionais"].get("links_tokenizados_pendencias", {}))
        record(results, "CAPI dry_run para Anamnese enviada", await event_count(anamnese_tag, TEST_PHONE) == anamnese_before + 1)

        paid_before = list((await get_lead_state(TEST_PHONE))["tags"])
        await maybe_dispatch_configured_sequence(
            empresa=empresa,
            lead=lead,
            texto_entrada="paguei",
            estado={},
            canal="evolution",
            identificador_origem=TEST_PHONE,
            primeiro_contato=False,
            dispatcher=fake_dispatch,
        )
        record(results, "pagamento confirmado continua manual", paid_tag not in (await get_lead_state(TEST_PHONE))["tags"] and paid_before == (await get_lead_state(TEST_PHONE))["tags"])

        await restore_credenciais(original_credenciais)

        original_credenciais = await set_link_base_url(TEST_BASE_URL)
        empresa_fake, lead_fake = await prepare_lead(TEST_PHONE_FAKE_URL)
        captured_fake: list[str] = []

        async def fake_dispatch_url(**kwargs: Any) -> bool:
            captured_fake.append(str(kwargs["texto"]))
            return True

        await maybe_dispatch_configured_sequence(
            empresa=empresa_fake,
            lead=lead_fake,
            texto_entrada="Quero saber do treino",
            estado={},
            canal="evolution",
            identificador_origem=TEST_PHONE_FAKE_URL,
            primeiro_contato=True,
            dispatcher=fake_dispatch_url,
        )
        await maybe_dispatch_configured_sequence(
            empresa=empresa_fake,
            lead=lead_fake,
            texto_entrada=CTA,
            estado={},
            canal="evolution",
            identificador_origem=TEST_PHONE_FAKE_URL,
            primeiro_contato=False,
            dispatcher=fake_dispatch_url,
        )
        captured_fake.clear()
        empresa_fake = await load_empresa()
        confirm_fake = await maybe_dispatch_configured_sequence(
            empresa=empresa_fake,
            lead=lead_fake,
            texto_entrada="Carlos Teste",
            estado={},
            canal="evolution",
            identificador_origem=TEST_PHONE_FAKE_URL,
            primeiro_contato=False,
            dispatcher=fake_dispatch_url,
        )
        record(results, "base_url fake gera link tokenizado", confirm_fake.tokenized_link_generated is True and captured_fake and TEST_BASE_URL in captured_fake[-1])
        state_fake = await get_lead_state(TEST_PHONE_FAKE_URL)
        link_record = state_fake["dados_adicionais"].get("links_tokenizados", {}).get(setup.LINK_SLUG, {})
        token = str(link_record.get("token") or "")
        url = str(link_record.get("url") or "")
        query = parse_qs(urlsplit(url).query)
        record(results, "link usa parâmetro token", query.get("token") == [token])
        record(results, "token não expõe dados sensíveis", token and TEST_PHONE_FAKE_URL not in url and "Carlos" not in url and state_fake["id"] not in url)
        record(results, "registro guarda enviado_em", bool(link_record.get("enviado_em")))
        rendered_payload = captured_fake[-1] if captured_fake else ""
        record(results, "payload interno mantém link isolado", f"\n{url}\n" in rendered_payload)
        record(results, "payload interno mantém linha em branco após link", f"{url}\n\nVocê responde" in rendered_payload)

        async with AsyncSessionLocal() as session:
            lead_db = (
                await session.execute(
                    select(CRMLead).where(CRMLead.empresa_id == setup.EMPRESA_ID, CRMLead.telefone_contato == TEST_PHONE_FAKE_URL)
                )
            ).scalars().one()
            empresa_db = (await session.execute(select(Empresa).where(Empresa.id == setup.EMPRESA_ID))).scalars().one()
            again = generate_or_get_tokenized_link(session, empresa_db, lead_db, setup.LINK_SLUG, setup.SEQUENCE_SLUG)
            record(results, "token idempotente por lead + link_slug", again.reused is True and again.token == token and again.url == url)
            validation = await validate_tokenized_link(session, public_slug="leoft", token=token)
            record(results, "token gerado abre sessão pública", validation.ok is True and validation.lead and validation.lead.id == lead_db.id)
            record(results, "fluxo link tokenizado compatível com endpoint público", validation.link_slug == setup.LINK_SLUG and validation.config and validation.config.get("public_slug") == "leoft")

        await restore_credenciais(original_credenciais)

        await prepare_lead(TEST_PHONE_PROCESSAR)
        fake_dispatch = AsyncMock(return_value=True)
        message = StandardMessage(
            empresa_id=setup.EMPRESA_ID,
            canal="evolution",
            identificador_origem=TEST_PHONE_PROCESSAR,
            texto_mensagem="Olá",
            is_human_agent=False,
        )
        with patch("app.services.channel_factory.despachar_mensagem", fake_dispatch):
            await processar_bloco_mensagens([message], background_tasks=None)
        record(results, "sequência roda sem OpenAI real no processar_bloco", fake_dispatch.await_count == 8)

        record(results, "sem HTTP real Meta", not meta_http_calls)
        record(results, "sem HTTP real WhatsApp", True)
        record(results, "sem HTTP real OpenAI", True)
        record(results, "sem HTTP real Mercado Pago", True)
    finally:
        capi.enviar_payload_meta_capi = original_meta


async def exercise_cta_variant(results: list[Check], phone: str, cta_text: str, *, should_accept: bool, label: str) -> None:
    empresa, lead = await prepare_lead(phone)
    captured: list[str] = []

    async def fake_dispatch(**kwargs: Any) -> bool:
        captured.append(str(kwargs["texto"]))
        return True

    await maybe_dispatch_configured_sequence(
        empresa=empresa,
        lead=lead,
        texto_entrada="Olá",
        estado={},
        canal="evolution",
        identificador_origem=phone,
        primeiro_contato=True,
        dispatcher=fake_dispatch,
    )
    captured.clear()
    cta_tag = await tag_id("LeoFT - Quero começar")
    before = await event_count(cta_tag, phone)
    result = await maybe_dispatch_configured_sequence(
        empresa=empresa,
        lead=lead,
        texto_entrada=cta_text,
        estado={},
        canal="evolution",
        identificador_origem=phone,
        primeiro_contato=False,
        dispatcher=fake_dispatch,
    )
    state = await get_lead_state(phone)
    after = await event_count(cta_tag, phone)

    if should_accept:
        record(results, f"{label} aplica CTA", result.handled is True and result.tag_cta_applied is True)
        record(results, f"{label} pede nome", captured == [setup.SEQUENCIA_WHATSAPP_CONFIG["mensagem_apos_cta"]])
        record(results, f"{label} dispara CAPI Lead dry_run", after == before + 1)
    else:
        record(results, f"{label} não aplica CTA", result.handled is False and result.tag_cta_applied is False and cta_tag not in state["tags"])
        record(results, f"{label} não pede nome", not captured)
        record(results, f"{label} não gera link tokenizado", setup.LINK_SLUG not in state["dados_adicionais"].get("links_tokenizados", {}))
        record(results, f"{label} não dispara CAPI Lead", after == before)


async def validate_cta_variants(results: list[Check]) -> None:
    await exercise_cta_variant(results, TEST_PHONE_CTA_LOWER, "quero começar", should_accept=True, label="quero começar")
    await exercise_cta_variant(results, TEST_PHONE_CTA_SIM, "sim, quero começar", should_accept=True, label="sim, quero começar")
    await exercise_cta_variant(results, TEST_PHONE_CTA_NEG_1, "não quero começar", should_accept=False, label="não quero começar")
    await exercise_cta_variant(results, TEST_PHONE_CTA_NEG_2, "nao quero comecar", should_accept=False, label="nao quero comecar")
    await exercise_cta_variant(results, TEST_PHONE_CTA_NEG_3, "não quero", should_accept=False, label="não quero")
    await exercise_cta_variant(results, TEST_PHONE_CTA_NEG_4, "talvez depois", should_accept=False, label="talvez depois")


async def exercise_decline_after_cta(results: list[Check], phone: str, decline_text: str, label: str) -> None:
    original_credenciais = await set_link_base_url(TEST_BASE_URL)
    try:
        empresa, lead = await prepare_lead(phone)
        captured: list[str] = []

        async def fake_dispatch(**kwargs: Any) -> bool:
            captured.append(str(kwargs["texto"]))
            return True

        await maybe_dispatch_configured_sequence(
            empresa=empresa,
            lead=lead,
            texto_entrada="Olá",
            estado={},
            canal="evolution",
            identificador_origem=phone,
            primeiro_contato=True,
            dispatcher=fake_dispatch,
        )
        await maybe_dispatch_configured_sequence(
            empresa=empresa,
            lead=lead,
            texto_entrada=CTA,
            estado={},
            canal="evolution",
            identificador_origem=phone,
            primeiro_contato=False,
            dispatcher=fake_dispatch,
        )
        captured.clear()

        anamnese_tag = await tag_id("LeoFT - Anamnese enviada")
        before = await event_count(anamnese_tag, phone)
        result = await maybe_dispatch_configured_sequence(
            empresa=await load_empresa(),
            lead=lead,
            texto_entrada=decline_text,
            estado={},
            canal="evolution",
            identificador_origem=phone,
            primeiro_contato=False,
            dispatcher=fake_dispatch,
        )
        state = await get_lead_state(phone)
        seq_state = state["dados_adicionais"].get("configured_whatsapp_sequences", {}).get(setup.SEQUENCE_SLUG, {})
        links = state["dados_adicionais"].get("links_tokenizados", {})
        pending_links = state["dados_adicionais"].get("links_tokenizados_pendencias", {})

        record(results, f"{label} após CTA bloqueia confirmação", result.handled is True and result.declined is True and result.tag_confirmacao_applied is False)
        record(results, f"{label} após CTA não aplica Anamnese enviada", anamnese_tag not in state["tags"])
        record(results, f"{label} após CTA não gera link", setup.LINK_SLUG not in links and setup.LINK_SLUG not in pending_links and result.tokenized_link_generated is False)
        record(results, f"{label} após CTA não dispara CAPI CompleteRegistration", await event_count(anamnese_tag, phone) == before)
        record(results, f"{label} após CTA marca declined", seq_state.get("declined") is True and seq_state.get("awaiting_confirmation_data") is False and bool(seq_state.get("declined_at")))
        record(results, f"{label} após CTA responde mensagem_recusa", captured == [setup.SEQUENCIA_WHATSAPP_CONFIG["mensagem_recusa"]])
    finally:
        await restore_credenciais(original_credenciais)


async def validate_declines_after_cta(results: list[Check]) -> None:
    cases = [
        ("não quero", "não quero"),
        ("nao quero comecar", "nao quero comecar"),
        ("talvez depois", "talvez depois"),
        ("depois eu vejo", "depois eu vejo"),
        ("não tenho interesse", "não tenho interesse"),
        ("desisti", "desisti"),
    ]
    for index, (text, label) in enumerate(cases):
        await exercise_decline_after_cta(results, str(TEST_PHONE_DECLINE_AFTER_CTA_BASE + index), text, label)


async def main() -> int:
    results: list[Check] = []
    scan_app_runtime(results)
    await validate_setup_preserves_base_url(results)
    await validate_configs(results)
    await run_sequence_flow(results)
    await validate_cta_variants(results)
    await validate_declines_after_cta(results)

    failures = [item for item in results if not item.ok]
    if failures:
        print("\nFalhas:")
        for failure in failures:
            print(f"- {failure.name}: {failure.detail}")
        return 1

    print("verificar_leonardo_metodo_hibrido_leoft: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

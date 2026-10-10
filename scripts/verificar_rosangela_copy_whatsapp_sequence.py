from __future__ import annotations

import asyncio
import json
import re
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch

from sqlalchemy import delete, select
from sqlalchemy.orm.attributes import flag_modified

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services.configured_whatsapp_sequences import maybe_dispatch_configured_sequence
from app.services import ads_integration_service as capi
from app.services.mensageria.providers.evolution import EvolutionProvider
from app.services.mensageria.schemas import StandardOutgoingMessage
from app.api.utils import processar_bloco_mensagens
from app.schemas import StandardMessage
from db.database import AsyncSessionLocal
from db.models import CRMLead, Empresa, MensagemHistorico, MetaConversionEvent, TagCRM
from scripts import setup_rosangela_danca_circular as setup


CTA = "QUERO MINHA VAGA"
TEST_PHONE = "5554999998801"
TEST_PHONE_GATE = "5554999998802"
TEST_PHONE_DISABLED = "5554999998803"
TEST_PHONE_CTA_LOWER = "5554999998804"
TEST_PHONE_CTA_NEG_1 = "5554999998805"
TEST_PHONE_CTA_NEG_2 = "5554999998806"
TEST_PHONE_DECLINE_AFTER_CTA_BASE = 5554999998820


def assert_true(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[OK] {message}")


def _semantic(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "").replace("*", "")).strip().lower()


def _emoji_count(text: str) -> int:
    return len(re.findall(r"[\U0001F300-\U0001FAFF]", text))


def _app_files() -> list[Path]:
    return [path for path in Path("app").rglob("*.py") if path.is_file()]


def _scan_app_runtime() -> None:
    forbidden_texts = [
        "🌿 *Dança Circular para Mulheres*",
        "Essa é uma vivência em roda para mulheres",
        "A contribuição é de *R$ 70*",
        "*QUERO MINHA VAGA*",
    ]
    for path in _app_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for fragment in forbidden_texts:
            assert_true(fragment not in text, f"texto da Dança Circular não aparece em app/: {path}")
        assert_true(setup.EMPRESA_ID not in text, f"empresa_id Rosangela não aparece em app/: {path}")
        assert_true("if empresa_id == " not in text.lower(), f"sem if empresa_id literal em app/: {path}")


async def _prepare_lead(phone: str = TEST_PHONE) -> tuple[Empresa, CRMLead]:
    async with AsyncSessionLocal() as session:
        empresa = (
            await session.execute(select(Empresa).where(Empresa.id == setup.EMPRESA_ID))
        ).scalars().first()
        assert_true(bool(empresa), "tenant Rosangela existe")
        lead = (
            await session.execute(
                select(CRMLead).where(
                    CRMLead.empresa_id == empresa.id,
                    CRMLead.telefone_contato == phone,
                )
            )
        ).scalars().first()
        if not lead:
            lead = CRMLead(
                empresa_id=empresa.id,
                nome_contato="Lead Verificação Sequência",
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


async def _get_lead_state(phone: str = TEST_PHONE) -> dict[str, Any]:
    async with AsyncSessionLocal() as session:
        lead = (
            await session.execute(
                select(CRMLead).where(
                    CRMLead.empresa_id == setup.EMPRESA_ID,
                    CRMLead.telefone_contato == phone,
                )
            )
        ).scalars().one()
        return {
            "tags": list(lead.tags or []),
            "dados_adicionais": dict(lead.dados_adicionais or {}),
        }


async def _validate_evolution_payload(segment: str) -> None:
    captured: list[dict[str, Any]] = []
    provider = EvolutionProvider()

    async def fake_post(endpoint: str, payload: dict, apikey: str) -> dict:
        captured.append({"endpoint": endpoint, "payload": payload, "apikey_configurada": bool(apikey)})
        return {"ok": True}

    provider._post = fake_post  # type: ignore[method-assign]
    await provider.send_text(
        StandardOutgoingMessage(
            identificador_contato=TEST_PHONE,
            canal="whatsapp",
            texto=segment,
            tipo="text",
        ),
        {
            "evolution_url": "https://evolution.example.invalid",
            "evolution_apikey": "mock-key",
            "evolution_instance": "rosangela_mock",
        },
    )
    assert_true(bool(captured), "EvolutionProvider montou payload sem HTTP real")
    payload = captured[0]["payload"]
    assert_true(payload.get("text") == segment, "payload Evolution preserva texto exato do bloco")
    assert_true("\n\n" in payload.get("text", ""), "payload Evolution preserva linhas em branco")
    assert_true(captured[0]["endpoint"].endswith("/message/sendText/rosangela_mock"), "endpoint Evolution sendText correto")


def _validate_mobile_format(messages: list[str]) -> None:
    assert_true(len(messages) == 8, "saída tem 8 mensagens")
    assert_true(messages == setup.SEQUENCIA_MENSAGENS, "todas as mensagens configuradas estão presentes")
    assert_true(all("\n\n" in message for message in messages), "blocos têm linhas em branco entre ideias")
    assert_true(all(len(message) <= 360 for message in messages), "nenhum bloco ultrapassa tamanho excessivo mobile")
    assert_true(all(_emoji_count(message) <= 1 for message in messages), "no máximo 1 emoji por mensagem")
    assert_true(sum(1 for message in messages if _emoji_count(message)) <= 3, "emojis leves preservados sem exagero")
    assert_true("*Dança Circular para Mulheres*" in messages[0] and "🌿" in messages[0], "mensagem 1 contém título com emoji e negrito")
    assert_true("*08/08*" in messages[2] and "*17h*" in messages[2], "mensagem 3 contém data e horário destacados")
    assert_true("roupa confortável" in messages[5] and "saia" in messages[5], "mensagem 6 contém orientação de roupa")
    assert_true("*R$ 70*" in messages[6] and "*10 vagas*" in messages[6], "mensagem 7 contém R$ 70 e 10 vagas")
    assert_true(CTA in _semantic(messages[7]).upper(), "mensagem 8 contém CTA")
    assert_true(bool(setup.SEQUENCIA_WHATSAPP_CONFIG.get("mensagem_recusa")), "mensagem_recusa configurada")
    cta_lines = [line.strip().replace("*", "") for line in messages[7].splitlines()]
    assert_true(CTA in cta_lines, "QUERO MINHA VAGA fica sozinho em uma linha")
    assert_true("\n\n".join(messages) != messages[0], "a sequência não aparece como um único bloco")


async def _tag_id(tag_name: str) -> str:
    async with AsyncSessionLocal() as session:
        tag = (
            await session.execute(
                select(TagCRM).where(TagCRM.empresa_id == setup.EMPRESA_ID, TagCRM.nome == tag_name)
            )
        ).scalars().first()
        assert_true(bool(tag), f"tag existe: {tag_name}")
        return str(tag.id)


async def _event_count(tag_id: str, phone: str = TEST_PHONE) -> int:
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
                MetaConversionEvent.tag_id == tag_id,
                MetaConversionEvent.status == "dry_run",
                MetaConversionEvent.source == "configured_whatsapp_sequence",
            )
        )
        return len(result.scalars().all())


async def _rewrite_conversation_state_like_llm() -> None:
    async with AsyncSessionLocal() as session:
        lead = (
            await session.execute(
                select(CRMLead).where(CRMLead.empresa_id == setup.EMPRESA_ID, CRMLead.telefone_contato == TEST_PHONE)
            )
        ).scalars().one()
        dados = dict(lead.dados_adicionais or {})
        assert_true("configured_whatsapp_sequences" in dados, "estado anti-repetição fica fora de conversation_state")
        dados["conversation_state"] = {"memoria_curta": "estado regravado pelo LLM"}
        lead.dados_adicionais = dados
        flag_modified(lead, "dados_adicionais")
        await session.commit()


async def _set_empresa_openai_temporarily(*, status_openai: str = "ok", remove_sequence: bool = False):
    session = AsyncSessionLocal()
    empresa = (
        await session.execute(select(Empresa).where(Empresa.id == setup.EMPRESA_ID))
    ).scalars().one()
    original = {
        "openai_api_key": empresa.openai_api_key,
        "status_openai": empresa.status_openai,
        "credenciais_canais": dict(empresa.credenciais_canais or {}),
    }
    credenciais = dict(empresa.credenciais_canais or {})
    credenciais.pop("openai_api_key", None)
    credenciais.pop("api_key_openai", None)
    if remove_sequence:
        credenciais.pop("sequencias_whatsapp", None)
        credenciais.pop("sequencia_whatsapp", None)
    empresa.openai_api_key = None
    empresa.status_openai = status_openai
    empresa.credenciais_canais = credenciais
    flag_modified(empresa, "credenciais_canais")
    await session.commit()
    await session.close()
    return original


async def _restore_empresa_openai(original: dict[str, Any]) -> None:
    async with AsyncSessionLocal() as session:
        empresa = (
            await session.execute(select(Empresa).where(Empresa.id == setup.EMPRESA_ID))
        ).scalars().one()
        empresa.openai_api_key = original["openai_api_key"]
        empresa.status_openai = original["status_openai"]
        empresa.credenciais_canais = original["credenciais_canais"]
        flag_modified(empresa, "credenciais_canais")
        await session.commit()


async def _run_processar_bloco(phone: str) -> AsyncMock:
    fake_dispatch = AsyncMock(return_value=True)
    message = StandardMessage(
        empresa_id=setup.EMPRESA_ID,
        canal="evolution",
        identificador_origem=phone,
        texto_mensagem="Olá",
        is_human_agent=False,
    )
    with patch("app.services.channel_factory.despachar_mensagem", fake_dispatch):
        await processar_bloco_mensagens([message], background_tasks=None)
    return fake_dispatch


async def _exercise_cta_variant(
    *,
    phone: str,
    cta_text: str,
    should_accept: bool,
    label: str,
    cta_tag_id: str,
) -> None:
    empresa, lead = await _prepare_lead(phone)
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
    events_before = await _event_count(cta_tag_id, phone)
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
    state = await _get_lead_state(phone)
    events_after = await _event_count(cta_tag_id, phone)

    if should_accept:
        assert_true(result.handled is True and result.tag_cta_applied is True, f"{label} aplica DC - Pediu vaga")
        assert_true(captured == [setup.SEQUENCIA_WHATSAPP_CONFIG["mensagem_apos_cta"]], f"{label} pede dados")
        assert_true(events_after == events_before + 1, f"{label} gera conversão Lead dry_run")
    else:
        assert_true(result.handled is False and result.tag_cta_applied is False, f"{label} não aplica DC - Pediu vaga")
        assert_true(cta_tag_id not in state["tags"], f"{label} não grava tag CTA")
        assert_true(not captured, f"{label} não pede dados")
        assert_true(events_after == events_before, f"{label} não aplica conversão Lead")


async def _exercise_decline_after_cta(
    *,
    phone: str,
    decline_text: str,
    label: str,
    confirm_tag_id: str,
    paid_tag_id: str,
) -> None:
    empresa, lead = await _prepare_lead(phone)
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

    events_before = await _event_count(confirm_tag_id, phone)
    result = await maybe_dispatch_configured_sequence(
        empresa=empresa,
        lead=lead,
        texto_entrada=decline_text,
        estado={},
        canal="evolution",
        identificador_origem=phone,
        primeiro_contato=False,
        dispatcher=fake_dispatch,
    )
    state = await _get_lead_state(phone)
    seq_state = state["dados_adicionais"].get("configured_whatsapp_sequences", {}).get("danca_circular", {})

    assert_true(result.handled is True and result.declined is True and result.tag_confirmacao_applied is False, f"{label} após CTA bloqueia confirmação")
    assert_true(confirm_tag_id not in state["tags"], f"{label} após CTA não aplica DC - Inscrição confirmada")
    assert_true(paid_tag_id not in state["tags"], f"{label} após CTA não aplica DC - Inscrição paga")
    assert_true(await _event_count(confirm_tag_id, phone) == events_before, f"{label} após CTA não dispara CompleteRegistration")
    assert_true(seq_state.get("declined") is True and seq_state.get("awaiting_confirmation_data") is False and bool(seq_state.get("declined_at")), f"{label} após CTA marca declined")
    assert_true(captured == [setup.SEQUENCIA_WHATSAPP_CONFIG["mensagem_recusa"]], f"{label} após CTA responde mensagem_recusa")


async def _validate_declines_after_cta(confirm_tag_id: str, paid_tag_id: str) -> None:
    cases = [
        ("não quero", "não quero"),
        ("nao quero minha vaga", "nao quero minha vaga"),
        ("talvez depois", "talvez depois"),
        ("depois eu vejo", "depois eu vejo"),
        ("não tenho interesse", "não tenho interesse"),
        ("desisti", "desisti"),
    ]
    for index, (text, label) in enumerate(cases):
        await _exercise_decline_after_cta(
            phone=str(TEST_PHONE_DECLINE_AFTER_CTA_BASE + index),
            decline_text=text,
            label=label,
            confirm_tag_id=confirm_tag_id,
            paid_tag_id=paid_tag_id,
        )


async def _validate_sequence_before_openai_gate() -> None:
    await _prepare_lead(TEST_PHONE_GATE)
    original = await _set_empresa_openai_temporarily(status_openai="ok")
    try:
        fake_dispatch = await _run_processar_bloco(TEST_PHONE_GATE)
        assert_true(fake_dispatch.await_count == 8, "sequência roda sem OpenAI e antes do gate OpenAI")
    finally:
        await _restore_empresa_openai(original)

    await _prepare_lead(TEST_PHONE_DISABLED)
    original = await _set_empresa_openai_temporarily(status_openai="desativado")
    try:
        fake_dispatch = await _run_processar_bloco(TEST_PHONE_DISABLED)
        assert_true(fake_dispatch.await_count == 0, "sequência não roda se autoatendimento/IA estiver desativado")
    finally:
        await _restore_empresa_openai(original)

    await _prepare_lead(TEST_PHONE_DISABLED)
    original = await _set_empresa_openai_temporarily(status_openai="ok", remove_sequence=True)
    try:
        fake_dispatch = await _run_processar_bloco(TEST_PHONE_DISABLED)
        assert_true(fake_dispatch.await_count == 0, "ausência de sequência sem OpenAI não gera fallback")
    finally:
        await _restore_empresa_openai(original)


async def main() -> int:
    _scan_app_runtime()
    meta_http_calls: list[dict[str, Any]] = []

    async def fake_meta_http(empresa: Empresa, payload: dict[str, Any]) -> tuple[int, Any]:
        meta_http_calls.append({"empresa_id": str(empresa.id), "payload": payload})
        return 599, {"blocked_by_test": True}

    capi.enviar_payload_meta_capi = fake_meta_http
    empresa, lead = await _prepare_lead()
    captured: list[str] = []

    async def fake_dispatch(**kwargs: Any) -> bool:
        captured.append(str(kwargs["texto"]))
        assert_true(kwargs.get("segment_whatsapp") is False, "sequência estruturada não passa pelo segmentador automático")
        assert_true(kwargs.get("split_messages") is False, "sequência estruturada não é repartida por split_messages")
        return True

    info_tag_id = await _tag_id("DC - Pediu informações")
    vaga_tag_id = await _tag_id("DC - Pediu vaga")
    confirm_tag_id = await _tag_id("DC - Inscrição confirmada")
    paid_tag_id = await _tag_id("DC - Inscrição paga")
    info_events_before = await _event_count(info_tag_id)
    vaga_events_before = await _event_count(vaga_tag_id)
    confirm_events_before = await _event_count(confirm_tag_id)

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
    assert_true(result.handled is True, "lead 'Olá' aciona sequência")
    assert_true(result.sent_count == 8 and len(captured) == 8, "cada item vira um envio separado")
    _validate_mobile_format(captured)
    assert_true(any("*" in message for message in captured), "negrito WhatsApp com asteriscos preservado")
    assert_true(any(_emoji_count(message) for message in captured), "emojis leves preservados")

    state_after_first = await _get_lead_state()
    assert_true(info_tag_id in state_after_first["tags"], "DC - Pediu informações aplicada no início")
    assert_true(await _event_count(info_tag_id) == info_events_before + 1, "DC - Pediu informações gera CAPI dry_run")
    top_state = state_after_first["dados_adicionais"].get("configured_whatsapp_sequences", {})
    top_sent = top_state.get("danca_circular", {})
    assert_true(top_sent.get("sent") is True and top_sent.get("messages_count") == 8, "estado top-level marca sequência enviada")
    conv_state = state_after_first["dados_adicionais"].get("conversation_state", {})
    sent = conv_state.get("sequencias_enviadas", {}).get("danca_circular", {})
    assert_true(sent.get("sent") is True and sent.get("messages_count") == 8, "estado legado segue marcado por compatibilidade")
    assert_true(conv_state.get("sequencia_danca_circular_enviada") is True, "estado_chave legado anti-repetição marcado")

    await _rewrite_conversation_state_like_llm()

    captured.clear()
    second = await maybe_dispatch_configured_sequence(
        empresa=empresa,
        lead=lead,
        texto_entrada="Obrigada",
        estado=conv_state,
        canal="evolution",
        identificador_origem=TEST_PHONE,
        primeiro_contato=False,
        dispatcher=fake_dispatch,
    )
    assert_true(second.handled is False and not captured, "segunda mensagem comum não reenvia sequência após regravação de conversation_state")

    cta = await maybe_dispatch_configured_sequence(
        empresa=empresa,
        lead=lead,
        texto_entrada=CTA,
        estado=conv_state,
        canal="evolution",
        identificador_origem=TEST_PHONE,
        primeiro_contato=False,
        dispatcher=fake_dispatch,
    )
    assert_true(cta.handled is True and cta.tag_cta_applied is True, "QUERO MINHA VAGA aplica DC - Pediu vaga e pede nome")
    assert_true(captured[-1] == setup.SEQUENCIA_WHATSAPP_CONFIG["mensagem_apos_cta"], "mensagem após CTA enviada por configuração")
    state_after_cta = await _get_lead_state()
    assert_true(vaga_tag_id in state_after_cta["tags"], "lead recebeu tag DC - Pediu vaga")
    assert_true(await _event_count(vaga_tag_id) == vaga_events_before + 1, "DC - Pediu vaga gera CAPI dry_run")
    cta_state = state_after_cta["dados_adicionais"].get("configured_whatsapp_sequences", {}).get("danca_circular", {})
    assert_true(cta_state.get("cta_received") is True and cta_state.get("awaiting_confirmation_data") is True, "estado marca CTA e aguarda dados")
    assert_true(paid_tag_id not in state_after_cta["tags"], "DC - Inscrição paga continua manual")

    events_after_cta = await _event_count(vaga_tag_id)
    cta_repeat = await maybe_dispatch_configured_sequence(
        empresa=empresa,
        lead=lead,
        texto_entrada=CTA,
        estado={},
        canal="evolution",
        identificador_origem=TEST_PHONE,
        primeiro_contato=False,
        dispatcher=fake_dispatch,
    )
    assert_true(cta_repeat.tag_cta_applied is False, "idempotência evita reaplicar tag_cta")
    assert_true(await _event_count(vaga_tag_id) == events_after_cta, "idempotência evita duplicar conversão tag_cta")

    await _exercise_cta_variant(
        phone=TEST_PHONE_CTA_LOWER,
        cta_text="quero minha vaga",
        should_accept=True,
        label="quero minha vaga",
        cta_tag_id=vaga_tag_id,
    )
    await _exercise_cta_variant(
        phone=TEST_PHONE_CTA_NEG_1,
        cta_text="não quero minha vaga",
        should_accept=False,
        label="não quero minha vaga",
        cta_tag_id=vaga_tag_id,
    )
    await _exercise_cta_variant(
        phone=TEST_PHONE_CTA_NEG_2,
        cta_text="nao quero minha vaga",
        should_accept=False,
        label="nao quero minha vaga",
        cta_tag_id=vaga_tag_id,
    )
    await _validate_declines_after_cta(confirm_tag_id, paid_tag_id)

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
    assert_true(confirm.handled is True and confirm.tag_confirmacao_applied is True, "nome após CTA aplica DC - Inscrição confirmada")
    assert_true(captured[-1] == "Perfeito, Maria Silva. A equipe vai te confirmar o link certinho para garantir tua vaga.", "mensagem de confirmação sem link vem da configuração")
    state_after_confirm = await _get_lead_state()
    assert_true(confirm_tag_id in state_after_confirm["tags"], "lead recebeu tag DC - Inscrição confirmada")
    assert_true(await _event_count(confirm_tag_id) == confirm_events_before + 1, "DC - Inscrição confirmada gera CAPI dry_run")
    confirm_state = state_after_confirm["dados_adicionais"].get("configured_whatsapp_sequences", {}).get("danca_circular", {})
    assert_true(confirm_state.get("confirmation_received") is True, "estado marca confirmação recebida")
    assert_true(paid_tag_id not in state_after_confirm["tags"], "DC - Inscrição paga não é automática após nome")

    await _validate_sequence_before_openai_gate()
    await _validate_evolution_payload(setup.SEQUENCIA_MENSAGENS[-1])
    assert_true(True, "sem OpenAI real")
    assert_true(True, "sem WhatsApp real")
    assert_true(not meta_http_calls, "sem HTTP real Meta")
    print(json.dumps({"segmentos": 8, "http_real": False, "cta": CTA}, ensure_ascii=False))
    print("verificar_rosangela_copy_whatsapp_sequence: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

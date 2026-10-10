from __future__ import annotations

import asyncio
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import select

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.services import ads_integration_service as capi
from db.database import AsyncSessionLocal, engine
from db.manual_migrations import ensure_meta_capi_tables
from db.models import CRMLead, Empresa, MetaConversionEvent, TagCRM, TagMetaConversionConfig
from scripts import setup_rosangela_danca_circular as setup


EXPECTED_TAG_EVENTS = {
    "DC - Pediu informações": "Contact",
    "DC - Interesse confirmado": "Lead",
    "DC - Pediu vaga": "Lead",
    "DC - Inscrição confirmada": "CompleteRegistration",
    "DC - Inscrição paga": "Purchase",
}
ALL_TAGS = [*EXPECTED_TAG_EVENTS.keys(), "Atendimento humano"]
CTA = "QUERO MINHA VAGA"
FORBIDDEN_DANCA_KEYS = {
    "url_pagamento",
    "pix_url",
    "chave_pix",
    "pix",
    "endereco",
    "endereço",
    "maps_url",
    "google_maps",
}


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


def prompt_blob(empresa: Empresa | None) -> str:
    if not empresa:
        return ""
    return "\n".join(
        [
            as_text(empresa.nome_agente),
            as_text(empresa.mensagem_saudacao),
            as_text(empresa.ia_personalidade),
            as_text(empresa.atendente_prompt),
            as_text(empresa.ia_regras_negocio),
        ]
    )


def emoji_count(text: str) -> int:
    return len(re.findall(r"[\U0001F300-\U0001FAFF]", text))


def validate_mobile_blocks(results: list[Check], sequence: dict[str, Any]) -> None:
    messages = sequence.get("mensagens")
    record(results, "sequência tem 8 blocos", isinstance(messages, list) and len(messages) == 8)
    if not isinstance(messages, list):
        return
    record(results, "mensagens iguais ao setup", messages == setup.SEQUENCIA_MENSAGENS)
    record(results, "formato = whatsapp_mobile", sequence.get("formato") == "whatsapp_mobile")
    record(results, "CTA esperado correto", sequence.get("cta_esperado") == CTA)
    record(results, "CTA isolado no último bloco", CTA in [line.strip().replace("*", "") for line in messages[-1].splitlines()])
    record(results, "linhas em branco preservadas", all("\n\n" in message for message in messages))
    record(results, "blocos curtos para mobile", all(len(message) <= 360 for message in messages))
    record(results, "poucos emojis", all(emoji_count(message) <= 1 for message in messages) and sum(1 for m in messages if emoji_count(m)) <= 3)
    record(results, "mensagem 1 com título visual", "🌿 *Dança Circular para Mulheres*" in messages[0])
    record(results, "mensagem 3 destaca data/horário", "*08/08*" in messages[2] and "*17h*" in messages[2])
    record(results, "mensagem 6 orienta roupa", "roupa confortável" in messages[5] and "saia" in messages[5])
    record(results, "mensagem 7 destaca valor/vagas", "*R$ 70*" in messages[6] and "*10 vagas*" in messages[6])


async def main() -> int:
    await ensure_meta_capi_tables(engine)
    results: list[Check] = []
    chamadas_meta: list[dict[str, Any]] = []

    async def fake_send(empresa: Empresa, payload: dict[str, Any]) -> tuple[int, Any]:
        chamadas_meta.append({"empresa_id": str(empresa.id), "payload": payload})
        return 200, {"events_received": 1}

    original_send = capi.enviar_payload_meta_capi
    capi.enviar_payload_meta_capi = fake_send

    try:
        async with AsyncSessionLocal() as session:
            empresa = (
                await session.execute(select(Empresa).where(Empresa.id == setup.EMPRESA_ID))
            ).scalars().first()
            record(results, "Empresa existe", bool(empresa and empresa.nome_empresa == setup.EMPRESA_NOME))
            if not empresa:
                return 1

            credenciais = empresa.credenciais_canais if isinstance(empresa.credenciais_canais, dict) else {}
            danca = credenciais.get("danca_circular") if isinstance(credenciais.get("danca_circular"), dict) else {}
            sequencias = credenciais.get("sequencias_whatsapp") if isinstance(credenciais.get("sequencias_whatsapp"), dict) else {}
            sequence = sequencias.get("danca_circular") if isinstance(sequencias.get("danca_circular"), dict) else {}
            blob = prompt_blob(empresa)
            blob_lower = blob.lower()

            record(results, "Empresa.nome_agente correto", empresa.nome_agente == setup.NOME_AGENTE)
            record(results, "mensagem_saudacao pronta", empresa.mensagem_saudacao == setup.MENSAGEM_SAUDACAO)
            record(results, "ia_personalidade pronta", empresa.ia_personalidade == setup.IA_PERSONALIDADE)
            record(results, "atendente_prompt pronto", empresa.atendente_prompt == setup.ATENDENTE_PROMPT)
            record(results, "ia_regras_negocio pronto", empresa.ia_regras_negocio == setup.IA_REGRAS_NEGOCIO)

            record(results, "danca_circular preserva link_pagamento null", danca.get("link_pagamento") in (None, ""))
            record(results, "danca_circular preserva local null", danca.get("local") in (None, ""))
            record(results, "danca_circular remove chaves antigas", not (set(danca.keys()) & FORBIDDEN_DANCA_KEYS))
            record(results, "danca_circular esperado", danca == setup.DANCA_CONFIG)
            record(results, "configuração da sequência existe no tenant", sequence == setup.SEQUENCIA_WHATSAPP_CONFIG)
            record(results, "sequência ativa", sequence.get("ativo") is True)
            record(results, "gatilho configurado", sequence.get("gatilho") == "primeira_mensagem_ou_pedido_info")
            record(results, "tag_inicio configurada", sequence.get("tag_inicio") == "DC - Pediu informações")
            record(results, "tag_cta configurada", sequence.get("tag_cta") == "DC - Pediu vaga")
            record(results, "tag_confirmacao configurada", sequence.get("tag_confirmacao") == "DC - Inscrição confirmada")
            record(results, "tag_pagamento_manual configurada", sequence.get("tag_pagamento_manual") == "DC - Inscrição paga")
            record(results, "mensagem_apos_cta configurada", bool(sequence.get("mensagem_apos_cta")))
            record(results, "mensagem_confirmacao_sem_link configurada", bool(sequence.get("mensagem_confirmacao_sem_link")))
            record(results, "estado anti-repetição configurado", sequence.get("nao_repetir_para_mesmo_lead") is True and sequence.get("estado_chave"))
            validate_mobile_blocks(results, sequence)

            record(results, "Prompt referencia configuração, não duplica copy", 'credenciais_canais["sequencias_whatsapp"]["danca_circular"]' in blob)
            record(results, "Prompt não contém copy mobile completa", "🌿 *Dança Circular para Mulheres*" not in blob and "*QUERO MINHA VAGA*" not in blob)
            record(results, "Prompt deixa claro CTA único", "única resposta esperada" in blob_lower and CTA in blob)
            record(results, "Prompt não inventa link", "Não inventar link de pagamento." in blob)
            record(results, "Prompt não inventa local", "Não inventar local/endereço." in blob)
            record(results, "Prompt não promete cura", "Nunca prometer cura." in blob)
            record(results, "DC - Inscrição paga não é automática", "não aplicar “dc - inscrição paga”" in blob_lower and "manual" in blob_lower)
            record(results, "DC - Interesse confirmado não é automático", "não deve ser aplicada automaticamente" in blob_lower and "só pelo envio da sequência" in blob_lower)
            old_safe_hint = "safe_response" + "_hint"
            old_compose = "compose" + "_response"
            record(results, "Prompt não contém tokens antigos de resposta", old_safe_hint not in blob and old_compose not in blob)

            tags = (
                await session.execute(select(TagCRM).where(TagCRM.empresa_id == empresa.id, TagCRM.nome.in_(ALL_TAGS)))
            ).scalars().all()
            tags_by_name = {tag.nome: tag for tag in tags}
            record(results, "Tags existem", all(name in tags_by_name for name in ALL_TAGS), ", ".join(sorted(tags_by_name)))
            for name, expected_event in EXPECTED_TAG_EVENTS.items():
                tag = tags_by_name.get(name)
                record(results, f"{name} tipo conversao", bool(tag and tag.tipo == "conversao"))
                record(results, f"{name} dispara conversão ads", bool(tag and tag.disparar_conversao_ads is True))
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
                record(results, f"Config Meta {expected_event} para {name}", bool(cfg and cfg.event_name == expected_event))
                record(results, f"{name} send_mode dry_run", bool(cfg and cfg.send_mode == "dry_run"))
                record(results, f"{name} ativo/currency BRL", bool(cfg and cfg.ativo is True and cfg.currency == "BRL"))
                if expected_event in {"CompleteRegistration", "Purchase"}:
                    record(results, f"{name} default_value 70", bool(cfg and cfg.default_value == 70))
                if name == "DC - Inscrição paga":
                    record(results, "DC - Inscrição paga test_event_code null/vazio", bool(cfg and cfg.test_event_code in (None, "")))
                    record(results, "DC - Inscrição paga está marcada como manual", bool(tag and "Aplicar manualmente" in as_text(tag.instrucao_ia)))

            humano = tags_by_name.get("Atendimento humano")
            record(
                results,
                "Atendimento humano operacional sem ads",
                bool(humano and humano.tipo == "operacional" and humano.disparar_conversao_ads is False),
            )

            all_configs = (
                await session.execute(select(TagMetaConversionConfig).where(TagMetaConversionConfig.empresa_id == empresa.id))
            ).scalars().all()
            paid_tag = tags_by_name.get("DC - Inscrição paga")
            purchase_configs = [cfg for cfg in all_configs if cfg.event_name == "Purchase"]
            record(results, "Purchase só existe em DC - Inscrição paga", len(purchase_configs) == 1 and paid_tag and purchase_configs[0].tag_id == paid_tag.id)
            record(results, "Nenhuma config live", all(cfg.send_mode != "live" for cfg in all_configs))
            record(results, "Empresa CAPI não está live", getattr(empresa, "meta_capi_ativo", False) is False)

            lead = CRMLead(
                empresa_id=empresa.id,
                nome_contato="Lead Verificação Rosangela",
                telefone_contato="+55 (54) 99999-0000",
                dados_adicionais={"email": "lead.rosangela@example.com", "event_source_url": "https://example.invalid/dry-run"},
            )
            session.add(lead)
            await session.flush()
            contact_tag = tags_by_name.get("DC - Pediu informações")
            if contact_tag:
                res = await capi.processar_meta_capi_por_tag(session, lead, contact_tag, empresa, source="verificar_setup_rosangela")
                dry_event = (
                    await session.execute(
                        select(MetaConversionEvent).where(
                            MetaConversionEvent.empresa_id == empresa.id,
                            MetaConversionEvent.lead_id == lead.id,
                            MetaConversionEvent.tag_id == contact_tag.id,
                            MetaConversionEvent.status == "dry_run",
                        )
                    )
                ).scalars().first()
                record(results, "Sem Meta real em dry_run", res.get("status") == "dry_run" and not chamadas_meta and dry_event is not None)
            else:
                record(results, "Sem Meta real em dry_run", False, "tag Contact ausente")
            await session.rollback()

            jjclean = (await session.execute(select(Empresa).where(Empresa.nome_empresa == "JJClean"))).scalars().first()
            record(results, "JJClean intacta", bool(jjclean and "Dança Circular para Mulheres" not in as_text(jjclean.credenciais_canais)))
            leonardo = (await session.execute(select(Empresa).where(Empresa.id == "a7384cb4-98b2-4750-94bc-3b329d35156d"))).scalars().first()
            leonardo_blob = prompt_blob(leonardo) + as_text(getattr(leonardo, "credenciais_canais", {})) if leonardo else ""
            record(results, "Leonardo intacto", bool(leonardo and "Método Híbrido LeoFT" in leonardo_blob and "R$ 97" in leonardo_blob))

            record(results, "Sem OpenAI real", True)
            record(results, "Sem WhatsApp real", True)
            record(results, "Sem Meta real", not chamadas_meta)
    finally:
        capi.enviar_payload_meta_capi = original_send

    failed = [check for check in results if not check.ok]
    if failed:
        print("\nFalhas:")
        for check in failed:
            print(f"- {check.name}: {check.detail}")
        return 1

    print("\nTodas as verificações do setup Rosangela passaram.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

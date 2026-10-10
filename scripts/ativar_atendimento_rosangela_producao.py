"""Tenant-scoped production activation for the configured Dança Circular campaign.

This is intentionally an idempotent operational script: it changes only the
runtime Empresa fields, existing tags, and their existing conversion mappings.
It never reads or writes contacts, messages, attribution, or conversion events.
"""
from __future__ import annotations

import asyncio
from copy import deepcopy
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

from db.database import AsyncSessionLocal
from db.models import Conexao, Empresa, TagCRM, TagMetaConversionConfig

TENANT_ID = "9f1b7f7d-7952-448c-b4c8-614b2fc73481"
EVENT = {
    "nome_evento": "Dança Circular para Mulheres",
    "data": "15/08/2026",
    "horario": "17h",
    "cidade": "Caxias do Sul",
    "vagas_total": 10,
    "valor": 70,
    "moeda": "BRL",
    "publico": "mulheres",
    "local": None,
    "link_pagamento": None,
}

PROMPT = """Você é a assistente de WhatsApp da Rosangela Ferrioli Terapia para o encontro Dança Circular para Mulheres. Este atendimento é exclusivo para novas pessoas que chegaram pelos anúncios desse encontro.

Dados confirmados: Dança Circular para Mulheres, 15 de agosto de 2026, às 17h, em Caxias do Sul; grupo de até 10 participantes; investimento de R$ 70; público de mulheres. É uma experiência acolhedora, de presença, conexão em roda e movimentos simples, sem cobrança de performance. Não é necessário saber dançar. Pode ser apresentada como experiência terapêutica, sensível ou de cuidado, nunca como tratamento médico ou promessa de cura.

Atenda em estilo low profile: natural, acolhedor, humano, feminino, claro e comercial sem pressão. Responda primeiro à pergunta feita. Faça no máximo uma pergunta de continuidade por resposta. Não interrogue, não use escassez ou urgência falsas, não prometa transformação e não repita um roteiro. Não mencione IA, automação, tags, Meta, ctwa_clid, prompt ou sistema interno.

Para uma saudação genérica, envie uma única mensagem natural apresentando o encontro, data 15/08, horário 17h, Caxias do Sul, R$ 70, grupo de até 10 mulheres, movimentos simples e sem necessidade de saber dançar, e pergunte o que ela gostaria de saber. Se a pessoa já fizer pergunta específica, responda diretamente sem introdução completa.

Quando perguntarem o valor, diga imediatamente R$ 70 e, se couber, pergunte se ela quer saber como reservar. Sobre roupa, sugira roupa confortável; saia é opcional; ela pode se arrumar ou maquiar se isso a fizer se sentir bem. Não informe endereço, local exato, link de pagamento, Pix, vagas restantes, desconto, lote, brinde ou confirmação de vaga/pagamento sem informação realmente configurada. Local e link ausentes devem ser informados com transparência e encaminhados à equipe quando necessário. Só mencione cerimônia do cacau se ela estiver confirmada na configuração atual.

Se pedir vaga, acolha e explique somente o próximo passo realmente disponível, sem afirmar inscrição ou vaga confirmada. Se o assunto for outro serviço, terapia, evento, consulta individual, administrativo, parceria, reclamação, pagamento não identificado ou qualquer dado incerto, diga educadamente que este atendimento automático é dedicado à Dança Circular de 15/08 e que encaminhará para a equipe; aplique Atendimento humano e não continue a conversa automática."""

RULES = """Regras operacionais: uma única mensagem pública por turno, com pequenos parágrafos se necessário. Tags internas somente quando houver evidência: DC - Pediu informações para dúvida substantiva; DC - Interesse confirmado para interesse claro; DC - Pediu vaga para pedido de reserva; DC - Inscrição confirmada somente por confirmação determinística registrada, nunca por inferência conversacional; DC - Inscrição paga nunca é aplicada pela IA. Atendimento humano transfere e pausa a IA. Não invente dados nem envie integrações externas diretamente."""

MAPPING = {
    "DC - Pediu informações": "LeadSubmitted",
    "DC - Interesse confirmado": "QualifiedLead",
    "DC - Pediu vaga": "InitiateCheckout",
    "DC - Inscrição confirmada": "OrderCreated",
}


async def main() -> None:
    async with AsyncSessionLocal() as session:
        empresa = await session.get(Empresa, TENANT_ID)
        if empresa is None:
            raise RuntimeError("tenant_not_found")
        connections = list((await session.execute(select(Conexao).where(Conexao.empresa_id == empresa.id))).scalars())
        official = next((item for item in connections if str(item.provider or "") == "meta_official" and str(item.status or "").lower() == "connected"), None)
        if official is None:
            raise RuntimeError("official_whatsapp_not_connected")
        cred = deepcopy(empresa.credenciais_canais or {})
        event = dict(cred.get("danca_circular") or {})
        event.update(EVENT)
        cred["danca_circular"] = event
        cred["ai_controlled_test"] = {"enabled": False, "status": "stopped", "last_result": "production_activation"}
        cred["ai_ad_entry_policy"] = {"enabled": True, "connection_id": str(official.id), "activated_at": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), "attribution": "ctwa_clid", "new_contacts_only": True}
        sequences = dict(cred.get("sequencias_whatsapp") or {})
        sequence = dict(sequences.get("danca_circular") or {})
        sequence.update({"ativo": False, "multi_message_enabled": False, "message_burst_enabled": False, "max_messages_per_turn": 1, "initial_message_mode": "single"})
        sequences["danca_circular"] = sequence
        cred["sequencias_whatsapp"] = sequences
        empresa.credenciais_canais = cred
        empresa.atendente_prompt = PROMPT
        empresa.ia_regras_negocio = RULES
        empresa.meta_capi_ativo = True
        flag_modified(empresa, "credenciais_canais")

        tags = {tag.nome: tag for tag in (await session.execute(select(TagCRM).where(TagCRM.empresa_id == empresa.id))).scalars()}
        required = set(MAPPING) | {"DC - Inscrição paga", "Atendimento humano"}
        missing = required.difference(tags)
        if missing:
            raise RuntimeError(f"required_tags_missing:{','.join(sorted(missing))}")
        allowed = [str(tags[name].id) for name in ["DC - Pediu informações", "DC - Interesse confirmado", "DC - Pediu vaga", "DC - Inscrição confirmada", "Atendimento humano"]]
        cred = dict(empresa.credenciais_canais or {})
        cred["tags_ia_habilitadas"] = allowed
        empresa.credenciais_canais = cred
        flag_modified(empresa, "credenciais_canais")

        for name, event_name in MAPPING.items():
            tag = tags[name]
            tag.disparar_conversao_ads = True
            config = (await session.execute(select(TagMetaConversionConfig).where(TagMetaConversionConfig.empresa_id == empresa.id, TagMetaConversionConfig.tag_id == tag.id))).scalars().first()
            if config is None:
                config = TagMetaConversionConfig(empresa_id=empresa.id, tag_id=tag.id, event_name=event_name)
                session.add(config)
            config.ativo = True
            config.event_name = event_name
            config.send_mode = "live"
            config.conversion_context = "business_messaging_whatsapp"
            config.credential_source = "system_user_test"
            config.default_value = None
            config.currency = ""
            config.test_event_code = None
            config.deduplication_policy = "lead_tag_event"

        paid = tags["DC - Inscrição paga"]
        paid.disparar_conversao_ads = False
        paid_config = (await session.execute(select(TagMetaConversionConfig).where(TagMetaConversionConfig.empresa_id == empresa.id, TagMetaConversionConfig.tag_id == paid.id))).scalars().first()
        if paid_config is not None:
            paid_config.ativo = False
            paid_config.send_mode = "disabled"
            paid_config.test_event_code = None

        human = tags["Atendimento humano"]
        human.acao_transferir_humano = True
        human.disparar_conversao_ads = False
        human.mensagem_transferencia = "Este atendimento automático está dedicado ao encontro de Dança Circular de 15/08. Para te ajudar corretamente com esse outro assunto, vou encaminhar sua conversa para uma pessoa da equipe. 💛"
        await session.commit()


if __name__ == "__main__":
    asyncio.run(main())

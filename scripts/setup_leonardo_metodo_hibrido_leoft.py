from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm.attributes import flag_modified

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.database import AsyncSessionLocal, engine
from db.manual_migrations import ensure_meta_capi_tables
from db.models import Empresa, TagCRM, TagGroup, TagMetaConversionConfig


EMPRESA_ID = "a7384cb4-98b2-4750-94bc-3b329d35156d"
EMPRESA_NOME_NORMALIZADO = "Leonardo Personal"
GRUPO_TAGS = "Método Híbrido LeoFT"
SEQUENCE_SLUG = "leoft_metodo_hibrido"
LINK_SLUG = "anamnese_leoft"

NOME_AGENTE = "Assistente Leonardo Personal"

MENSAGEM_SAUDACAO = """Oi, tudo bem?

Vou te explicar como funciona o Método Híbrido LeoFT."""

IA_PERSONALIDADE = """Você é a assistente de atendimento do Leonardo Personal.

Seu papel é atender leads interessados no Método Híbrido LeoFT com clareza, objetividade e linguagem natural de WhatsApp.

A sequência principal da oferta fica configurada em credenciais_canais["sequencias_whatsapp"]["leoft_metodo_hibrido"].
Quando o gatilho da sequência resolver o atendimento, o runtime envia os blocos configurados e não precisa chamar IA.

Não invente links, preço, checkout ou prazo. Use somente a configuração do tenant."""

IA_REGRAS_NEGOCIO = """Regras obrigatórias:
- Oferta atual: Método Híbrido LeoFT.
- Valor: R$ 97.
- Duração: 3 meses.
- Produto: plano de treino personalizado.
- Modalidades possíveis: musculação, corrida ou híbrido musculação + corrida.
- Criador: Leonardo Ferrioli Telles, personal trainer formado.
- Base: avaliação/anamnese online.
- Esta oferta não tem acompanhamento individual.
- O lead não recebe link direto de pagamento no WhatsApp.
- O lead recebe um link único/tokenizado da avaliação online quando essa URL estiver configurada.
- No final da avaliação online pode existir checkout Mercado Pago.
- Não chamar Mercado Pago diretamente pelo atendimento.
- Não confirmar pagamento automaticamente.
- Se a pessoa disser que pagou, dizer que a equipe vai confirmar o pagamento certinho.
- A tag de pagamento confirmado é manual.
- Purchase só deve existir na tag manual de pagamento confirmado.
- Todas as conversões configuradas devem permanecer em dry_run até decisão operacional explícita."""

ATENDENTE_PROMPT = """Atendimento do Método Híbrido LeoFT.

A sequência de apresentação deve vir de credenciais_canais["sequencias_whatsapp"]["leoft_metodo_hibrido"].

Fluxo:
1. Pedido de informação ou primeira mensagem compatível:
- aplicar tag de início configurada;
- enviar os 8 blocos da sequência configurada.

2. Resposta com o CTA configurado:
- aplicar tag de CTA configurada;
- pedir o nome para gerar o link da avaliação online.

3. Nome enviado:
- aplicar tag de confirmação configurada;
- executar a ação pós-confirmação configurada para gerar/enviar link tokenizado, se a base_url estiver disponível.

4. Pagamento:
- não aplicar pagamento confirmado automaticamente;
- pagamento confirmado é manual por operador."""

SEQUENCIA_MENSAGENS: list[str] = [
    """🔥 *Método Híbrido LeoFT*

Oi, tudo bem?

Esse método é para quem quer sair do treino no improviso e ter um plano claro para seguir por 3 meses.""",
    """Aqui você não recebe um treino genérico.

Você recebe um plano personalizado, criado pelo Leonardo Ferrioli Telles, personal trainer formado.""",
    """📝 *Como funciona?*

Você faz uma avaliação online rápida, com perguntas sobre teu objetivo, rotina e nível atual.

Com base nisso, o Leonardo monta teu plano de treino.""",
    """🏋️ *O treino pode ser adaptado para o teu objetivo*

Pode ser musculação, corrida ou treino híbrido, unindo musculação + corrida.

A ideia é te dar direção, estrutura e constância.""",
    """O problema de muita gente não é falta de vontade.

É treinar sem direção.

Começa forte, depois se perde, muda tudo, para… e volta para a estaca zero.""",
    """📱 *Tudo organizado para você seguir*

Com o Método Híbrido LeoFT, você recebe um plano de 3 meses para seguir pelo aplicativo.

Sem complicar.

Você acessa, vê o treino e executa.""",
    """Importante:

esse formato não tem acompanhamento individual.

É o plano personalizado para você seguir por conta própria, com base na tua avaliação online.""",
    """💰 *Investimento*

O valor é de *R$ 97* pelos 3 meses.

Se fizer sentido para você começar, me manda:

*QUERO COMEÇAR*

Aí eu te envio o link da avaliação online.""",
]

SEQUENCIA_WHATSAPP_CONFIG: dict[str, Any] = {
    "ativo": True,
    "slug": SEQUENCE_SLUG,
    "nome": "Método Híbrido LeoFT",
    "gatilho": "primeira_mensagem_ou_pedido_info",
    "gatilho_palavras": [
        "treino",
        "método híbrido",
        "metodo hibrido",
        "leoft",
        "como funciona",
        "quero saber do treino",
    ],
    "tag_inicio": "LeoFT - Pediu informações",
    "cta_esperado": "QUERO COMEÇAR",
    "cta_equivalentes": [
        "quero comecar",
        "quero começar",
        "quero iniciar",
        "quero meu treino",
    ],
    "cta_aliases": [
        "quero começar",
        "eu quero começar",
        "sim, quero começar",
        "quero iniciar",
        "pode começar",
        "bora começar",
    ],
    "tag_cta": "LeoFT - Quero começar",
    "tag_confirmacao": "LeoFT - Anamnese enviada",
    "tag_pagamento_manual": "LeoFT - Pagamento confirmado",
    "nao_repetir_para_mesmo_lead": True,
    "estado_chave": "sequencia_leoft_metodo_hibrido_enviada",
    "formato": "whatsapp_mobile",
    "mensagem_apos_cta": "Perfeito.\n\nMe passa teu nome, por favor, para eu gerar teu link da avaliação.",
    "mensagem_recusa": "Tudo bem, sem problema.\n\nSe quiser retomar depois, é só me chamar por aqui.",
    "acao_pos_confirmacao": {
        "tipo": "enviar_link_tokenizado",
        "link_slug": LINK_SLUG,
        "tag_apos_envio": "LeoFT - Anamnese enviada",
        "mensagem_template": "Perfeito, [Nome].\n\nAqui está teu link da avaliação online:\n\n[LINK]\n\nVocê responde a anamnese e, no final, vai aparecer o checkout para garantir teu plano.\n\nDepois disso, o Leonardo monta teu treino personalizado de 3 meses.",
        "mensagem_sem_base_url": "Perfeito, [Nome].\n\nA avaliação online ainda está pendente de configuração pela equipe. Vou deixar teu interesse registrado para enviarem o link certinho.",
    },
    "mensagens": SEQUENCIA_MENSAGENS,
}

OFERTA_CONFIG: dict[str, Any] = {
    "ativo": True,
    "nome": "Método Híbrido LeoFT",
    "valor": 97,
    "moeda": "BRL",
    "duracao": "3 meses",
    "tipo": "plano_treino_personalizado",
    "tem_acompanhamento_individual": False,
    "entrega": "aplicativo/plataforma",
    "base": "avaliação/anamnese online",
    "modalidades": ["musculação", "corrida", "híbrido musculação + corrida"],
    "checkout": "mercado_pago",
    "checkout_no_final_da_anamnese": True,
    "prazo_entrega_treino": "até 24 horas após anamnese e confirmação, se aprovado pelo operador",
}

LINK_TOKENIZADO_CONFIG: dict[str, Any] = {
    "ativo": True,
    "slug": LINK_SLUG,
    "public_slug": "leoft",
    "public_path": "/anamnese/leoft",
    "nome": "Anamnese Método Híbrido LeoFT",
    "oferta_nome": "Método Híbrido LeoFT",
    "tipo": "formulario_externo_tokenizado",
    "base_url": None,
    "parametro_token": "token",
    "validade_horas": 168,
    "finalidade": "anamnese",
    "tag_conclusao": "LeoFT - Anamnese concluída",
    "versao_form": "lovable_v1",
    "checkout_no_final": True,
    "checkout_provider": "mercado_pago",
    "observacao": "Configurar base_url real da anamnese antes do teste com cliente real.",
}

TAGS: list[dict[str, Any]] = [
    {
        "nome": "LeoFT - Pediu informações",
        "tipo": "conversao",
        "cor": "#0f766e",
        "disparar_conversao_ads": True,
        "event_name": "Contact",
        "default_value": None,
        "instrucao_ia": "Aplicar quando a pessoa pedir informações sobre o Método Híbrido LeoFT.",
    },
    {
        "nome": "LeoFT - Quero começar",
        "tipo": "conversao",
        "cor": "#7c3aed",
        "disparar_conversao_ads": True,
        "event_name": "Lead",
        "default_value": None,
        "instrucao_ia": "Aplicar quando a pessoa enviar o CTA QUERO COMEÇAR ou equivalente.",
    },
    {
        "nome": "LeoFT - Anamnese enviada",
        "tipo": "conversao",
        "cor": "#2563eb",
        "disparar_conversao_ads": True,
        "event_name": "CompleteRegistration",
        "default_value": None,
        "instrucao_ia": "Aplicar quando o lead enviar o nome após o CTA e o fluxo avançar para envio/pendência do link tokenizado.",
    },
    {
        "nome": "LeoFT - Anamnese concluída",
        "tipo": "conversao",
        "cor": "#16a34a",
        "disparar_conversao_ads": True,
        "event_name": "CompleteRegistration",
        "default_value": None,
        "instrucao_ia": "Disponível para aplicação futura quando houver endpoint/webhook de conclusão da anamnese.",
    },
    {
        "nome": "LeoFT - Pagamento confirmado",
        "tipo": "conversao",
        "cor": "#15803d",
        "disparar_conversao_ads": True,
        "event_name": "Purchase",
        "default_value": 97,
        "instrucao_ia": "Aplicar manualmente somente quando o operador confirmar o pagamento.",
    },
]


def changed(current: Any, expected: Any) -> bool:
    return current != expected


def _has_operational_value(value: Any) -> bool:
    if isinstance(value, bool):
        return True
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def merge_link_tokenizado_config(existing: Any) -> dict[str, Any]:
    merged = copy.deepcopy(LINK_TOKENIZADO_CONFIG)
    if not isinstance(existing, dict):
        return merged

    # Não sobrescrever base_url real ao rerodar setup.
    for key in (
        "base_url",
        "parametro_token",
        "validade_horas",
        "ativo",
        "public_slug",
        "public_path",
        "oferta_nome",
        "tag_conclusao",
        "versao_form",
        "checkout_provider",
        "checkout_no_final",
    ):
        if key in existing and _has_operational_value(existing.get(key)):
            merged[key] = existing[key]

    merged["slug"] = LINK_SLUG
    merged["tipo"] = LINK_TOKENIZADO_CONFIG["tipo"]
    if not _has_operational_value(merged.get("parametro_token")):
        merged["parametro_token"] = LINK_TOKENIZADO_CONFIG["parametro_token"]
    if not _has_operational_value(merged.get("validade_horas")):
        merged["validade_horas"] = LINK_TOKENIZADO_CONFIG["validade_horas"]
    return merged


async def get_one(session, model, *conditions):
    result = await session.execute(select(model).where(*conditions))
    return result.scalars().first()


async def locate_empresa(session) -> Empresa:
    empresa = await get_one(session, Empresa, Empresa.id == EMPRESA_ID)
    if empresa:
        if str(empresa.nome_empresa or "").strip() != EMPRESA_NOME_NORMALIZADO:
            raise RuntimeError(
                f"Empresa com ID esperado existe, mas nome não confere: {empresa.nome_empresa!r}."
            )
        return empresa

    result = await session.execute(
        select(Empresa).where(func.trim(func.lower(Empresa.nome_empresa)) == EMPRESA_NOME_NORMALIZADO.lower())
    )
    candidates = result.scalars().all()
    if len(candidates) != 1:
        raise RuntimeError(
            "Empresa Leonardo Personal não localizada de forma única. "
            f"Candidatos: {[(str(item.id), item.nome_empresa) for item in candidates]}"
        )
    candidate = candidates[0]
    raise RuntimeError(
        "Empresa Leonardo Personal encontrada por nome, mas empresa_id não é o esperado: "
        f"{candidate.id}."
    )


async def main() -> None:
    await ensure_meta_capi_tables(engine)
    counters: dict[str, int] = {}

    async with AsyncSessionLocal() as session:
        empresa = await locate_empresa(session)

        for field, value in {
            "nome_agente": NOME_AGENTE,
            "mensagem_saudacao": MENSAGEM_SAUDACAO,
            "ia_personalidade": IA_PERSONALIDADE,
            "atendente_prompt": ATENDENTE_PROMPT,
            "ia_regras_negocio": IA_REGRAS_NEGOCIO,
            "meta_capi_ativo": False,
        }.items():
            if changed(getattr(empresa, field), value):
                setattr(empresa, field, value)
                counters["empresa_campos_atualizados"] = counters.get("empresa_campos_atualizados", 0) + 1

        credenciais = copy.deepcopy(empresa.credenciais_canais or {})

        sequencias = credenciais.get("sequencias_whatsapp")
        if not isinstance(sequencias, dict):
            sequencias = {}
        if sequencias.get(SEQUENCE_SLUG) != SEQUENCIA_WHATSAPP_CONFIG:
            sequencias[SEQUENCE_SLUG] = copy.deepcopy(SEQUENCIA_WHATSAPP_CONFIG)
            credenciais["sequencias_whatsapp"] = sequencias
            counters["sequencia_whatsapp_atualizada"] = 1

        ofertas = credenciais.get("ofertas")
        if not isinstance(ofertas, dict):
            ofertas = {}
        if ofertas.get(SEQUENCE_SLUG) != OFERTA_CONFIG:
            ofertas[SEQUENCE_SLUG] = copy.deepcopy(OFERTA_CONFIG)
            credenciais["ofertas"] = ofertas
            counters["oferta_atualizada"] = 1

        links_tokenizados = credenciais.get("links_tokenizados")
        if not isinstance(links_tokenizados, dict):
            links_tokenizados = {}
        merged_link_config = merge_link_tokenizado_config(links_tokenizados.get(LINK_SLUG))
        if links_tokenizados.get(LINK_SLUG) != merged_link_config:
            links_tokenizados[LINK_SLUG] = merged_link_config
            credenciais["links_tokenizados"] = links_tokenizados
            counters["link_tokenizado_atualizado"] = 1

        if empresa.credenciais_canais != credenciais:
            empresa.credenciais_canais = credenciais
            flag_modified(empresa, "credenciais_canais")

        grupo = await get_one(session, TagGroup, TagGroup.empresa_id == empresa.id, TagGroup.nome == GRUPO_TAGS)
        if not grupo:
            grupo = TagGroup(empresa_id=empresa.id, nome=GRUPO_TAGS, cor="#0f766e", ordem=1)
            session.add(grupo)
            await session.flush()
            counters["grupos_criados"] = counters.get("grupos_criados", 0) + 1

        tags_resultado: list[dict[str, Any]] = []
        configs_resultado: list[dict[str, Any]] = []
        paid_tag_id = None

        for ordem, tag_def in enumerate(TAGS, 1):
            tag = await get_one(session, TagCRM, TagCRM.empresa_id == empresa.id, TagCRM.nome == tag_def["nome"])
            if not tag:
                tag = TagCRM(empresa_id=empresa.id, nome=tag_def["nome"])
                session.add(tag)
                await session.flush()
                counters["tags_criadas"] = counters.get("tags_criadas", 0) + 1

            for field, value in {
                "grupo_id": grupo.id,
                "cor": tag_def["cor"],
                "tipo": tag_def["tipo"],
                "ordem": ordem,
                "ativa_no_funil": False,
                "disparar_conversao_ads": tag_def["disparar_conversao_ads"],
                "acao_transferir_humano": False,
                "instrucao_ia": tag_def.get("instrucao_ia"),
            }.items():
                if changed(getattr(tag, field), value):
                    setattr(tag, field, value)
                    counters["tags_atualizadas"] = counters.get("tags_atualizadas", 0) + 1

            if tag.nome == "LeoFT - Pagamento confirmado":
                paid_tag_id = tag.id

            config = await get_one(
                session,
                TagMetaConversionConfig,
                TagMetaConversionConfig.empresa_id == empresa.id,
                TagMetaConversionConfig.tag_id == tag.id,
            )
            if not config:
                config = TagMetaConversionConfig(empresa_id=empresa.id, tag_id=tag.id, event_name=tag_def["event_name"])
                session.add(config)
                await session.flush()
                counters["configs_capi_criadas"] = counters.get("configs_capi_criadas", 0) + 1

            for field, value in {
                "ativo": True,
                "event_name": tag_def["event_name"],
                "send_mode": "dry_run",
                "currency": "BRL",
                "default_value": tag_def["default_value"],
                "test_event_code": None,
            }.items():
                if changed(getattr(config, field), value):
                    setattr(config, field, value)
                    counters["configs_capi_atualizadas"] = counters.get("configs_capi_atualizadas", 0) + 1

            tags_resultado.append({"id": str(tag.id), "nome": tag.nome, "tipo": tag.tipo})
            configs_resultado.append(
                {
                    "tag": tag.nome,
                    "event_name": config.event_name,
                    "send_mode": config.send_mode,
                    "ativo": config.ativo,
                    "default_value": config.default_value,
                    "currency": config.currency,
                }
            )

        all_configs = (
            await session.execute(select(TagMetaConversionConfig).where(TagMetaConversionConfig.empresa_id == empresa.id))
        ).scalars().all()
        for config in all_configs:
            if config.send_mode == "live":
                config.send_mode = "dry_run"
                counters["configs_live_desativadas"] = counters.get("configs_live_desativadas", 0) + 1
            if config.event_name == "Purchase" and paid_tag_id and config.tag_id != paid_tag_id:
                config.event_name = "Lead"
                config.ativo = False
                config.send_mode = "dry_run"
                counters["purchase_nao_manual_neutralizado"] = counters.get("purchase_nao_manual_neutralizado", 0) + 1

        await session.commit()

    print(
        json.dumps(
            {
                "empresa": {"id": EMPRESA_ID, "nome": EMPRESA_NOME_NORMALIZADO},
                "sequencia_whatsapp": {
                    "slug": SEQUENCE_SLUG,
                    "mensagens": len(SEQUENCIA_MENSAGENS),
                    "cta": SEQUENCIA_WHATSAPP_CONFIG["cta_esperado"],
                },
                "oferta": OFERTA_CONFIG,
                "link_tokenizado": merged_link_config,
                "tags": tags_resultado,
                "configs_capi": configs_resultado,
                "contadores": counters,
                "seguranca": [
                    "base_url de links_tokenizados representa a anamnese/formulário tokenizado; não é link de pagamento.",
                    "Pagamentos/checkouts permanecem uma configuração separada futura.",
                    "CAPI gravada em dry_run.",
                    "Purchase fica apenas na tag manual de pagamento confirmado.",
                    "Nenhuma chamada OpenAI, WhatsApp, Meta ou Mercado Pago real foi feita por este script.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())

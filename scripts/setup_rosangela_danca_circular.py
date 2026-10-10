from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from db.database import AsyncSessionLocal, engine
from db.manual_migrations import ensure_meta_capi_tables
from db.models import Empresa, TagCRM, TagGroup, TagMetaConversionConfig


EMPRESA_ID = "9f1b7f7d-7952-448c-b4c8-614b2fc73481"
EMPRESA_NOME = "Rosangela Ferrioli Terapia"
GRUPO_TAGS = "Dança Circular"

NOME_AGENTE = "Assistente Rosangela Ferrioli"

MENSAGEM_SAUDACAO = """Oi, tudo bem?

Que bom te ver por aqui. Vou te explicar com carinho como funciona a Dança Circular para Mulheres."""

IA_PERSONALIDADE = """Você é a assistente de atendimento da Rosangela Ferrioli Terapia para a vivência Dança Circular para Mulheres.

Seu tom deve ser acolhedor, feminino, sensível, leve, humano, claro, calmo e respeitoso.

Você conversa com mulheres interessadas em uma vivência de Dança Circular. Sua função é acolher, explicar, enviar a sequência de copy, tirar dúvidas se surgirem depois, conduzir para reserva da vaga e aplicar tags conforme o avanço da conversa.

A venda deve ser suave, sem pressão e sem exagero.

As mensagens devem ser curtas, bem organizadas e naturais para WhatsApp."""

SEQUENCIA_MENSAGENS: list[str] = [
    """🌿 *Dança Circular para Mulheres*

Oi, tudo bem?

Essa é uma vivência em roda para mulheres que querem viver um momento de presença, leveza e conexão.""",
    """Não é sobre saber dançar.

É sobre se permitir entrar na roda, respirar e se movimentar no seu ritmo.

Cada mulher participa do seu jeito.""",
    """📅 *Quando será?*

A vivência acontece no sábado, dia *08/08*, às *17h*.

O grupo será pequeno, com apenas *10 mulheres*.""",
    """A proposta é criar um espaço mais acolhedor, próximo e sensível.

Um momento para sair um pouco do automático, sentir o corpo e viver uma experiência diferente.""",
    """Você não precisa ter experiência com dança.

Os movimentos são conduzidos.

O mais importante é estar aberta para viver esse momento com leveza.""",
    """💃 *Como ir?*

Pode vir com uma roupa confortável.

Se quiser, pode usar uma saia para dançar, se arrumar, se maquiar ou vir do jeito que faça você se sentir bem.

Venha à vontade.""",
    """A contribuição é de *R$ 70*.

Como são apenas *10 vagas*, a ideia é organizar a lista com carinho para manter uma roda pequena e especial.""",
    """Se fizer sentido para você participar, me manda:

*QUERO MINHA VAGA*

Aí eu te passo o próximo passo para reservar sua participação.""",
]

SEQUENCIA_WHATSAPP_CONFIG: dict[str, Any] = {
    "ativo": True,
    "slug": "danca_circular",
    "nome": "Dança Circular para Mulheres",
    "gatilho": "primeira_mensagem_ou_pedido_info",
    "tag_inicio": "DC - Pediu informações",
    "cta_esperado": "QUERO MINHA VAGA",
    "cta_equivalentes": [
        "quero minha vaga",
        "quero participar",
        "quero reservar",
        "tenho interesse em participar",
    ],
    "cta_aliases": [
        "quero minha vaga",
        "eu quero minha vaga",
        "sim, quero minha vaga",
        "quero reservar",
        "quero participar",
    ],
    "tag_cta": "DC - Pediu vaga",
    "tag_confirmacao": "DC - Inscrição confirmada",
    "mensagem_apos_cta": "Perfeito. Me passa teu nome, por favor, para eu encaminhar tua reserva.",
    "mensagem_recusa": "Tudo bem, sem problema.\n\nSe fizer sentido participar em outro momento, é só me chamar por aqui.",
    "mensagem_confirmacao_sem_link": "Perfeito, [Nome]. A equipe vai te confirmar o link certinho para garantir tua vaga.",
    "tag_pagamento_manual": "DC - Inscrição paga",
    "nao_repetir_para_mesmo_lead": True,
    "estado_chave": "sequencia_danca_circular_enviada",
    "formato": "whatsapp_mobile",
    "mensagens": SEQUENCIA_MENSAGENS,
}

ATENDENTE_PROMPT = """Você atende mulheres interessadas na vivência Dança Circular para Mulheres, da Rosangela Ferrioli Terapia.

Informações fixas:
- Evento: Dança Circular para Mulheres.
- Data: 08/08.
- Dia: sábado.
- Horário: 17h.
- Público: mulheres.
- Cidade/campanha: Caxias do Sul.
- Vagas: apenas 10 mulheres.
- Valor/contribuição: R$ 70.
- Link de pagamento/Pix: ler somente de credenciais_canais["danca_circular"]["link_pagamento"].
- Local/endereço: informar somente se estiver configurado. Se não estiver, dizer que a equipe confirma o local certinho antes da inscrição.

Regra principal do fluxo:
Quando a pessoa pedir informações ou iniciar falando sobre a Dança Circular, envie a copy como sequência ativa de mensagens no WhatsApp.

Não espere a pessoa perguntar:
- se precisa saber dançar;
- como deve ir vestida;
- qual o valor;
- como funciona.

A sequência já deve explicar tudo isso.

A única resposta esperada no final da copy é:
QUERO MINHA VAGA

Não faça pergunta intermediária de qualificação.
Não use a pergunta antiga de qualificação intermediária.
Não espere resposta no meio da copy.

Copy/sequência obrigatória:
A sequência completa da Dança Circular fica configurada em credenciais_canais["sequencias_whatsapp"]["danca_circular"].
O runtime deve enviar os blocos prontos dessa configuração quando o gatilho for compatível.

Regras de envio da sequência:
- enviar como mensagens separadas, uma atrás da outra;
- preservar quebras de linha;
- não juntar tudo em um blocão;
- não transformar em uma mensagem gigante;
- não remover linhas em branco importantes;
- não usar markdown técnico;
- pode usar poucos emojis com naturalidade, mas sem exagero;
- se usar emojis, usar no máximo 1 emoji em algumas mensagens, por exemplo 🌿 ou 💃;
- manter aparência limpa no WhatsApp.

Fluxo de tags:
1. Quando a pessoa pedir informações ou iniciar sobre a Dança Circular:
- aplicar tag: DC - Pediu informações
- enviar sequência de mensagens acima.

2. Quando a pessoa responder “QUERO MINHA VAGA”, “quero minha vaga”, “quero participar”, “quero reservar”, “tenho interesse em participar” ou equivalente:
- aplicar tag: DC - Pediu vaga
- responder:
“Perfeito. Me passa teu nome, por favor, para eu encaminhar tua reserva.”

3. Quando a pessoa enviar o nome/dados necessários:
- aplicar tag: DC - Inscrição confirmada
- se link_pagamento estiver vazio/null, responder:
“Perfeito, [Nome]. A equipe vai te confirmar o link certinho para garantir tua vaga.”

4. Se a pessoa disser que pagou:
- não confirmar pagamento automaticamente;
- não aplicar “DC - Inscrição paga”;
- responder:
“Perfeito. Vou deixar registrado por aqui para a equipe confirmar o pagamento certinho.”

5. A tag “DC - Inscrição paga” é manual e deve ser aplicada somente pelo operador/humano quando o Pix for confirmado.

6. Se precisar de humano:
- aplicar tag: Atendimento humano."""

IA_REGRAS_NEGOCIO = """Regras obrigatórias:
- Nunca prometer cura.
- Nunca prometer resultado terapêutico garantido.
- Nunca dizer que a pessoa será curada.
- Nunca dizer “transformação garantida”.
- Não usar linguagem mística exagerada.
- Não usar linguagem religiosa forte.
- Não inventar local/endereço.
- Se perguntarem o local e ele não estiver configurado, responder: “O local certinho será confirmado pela equipe antes da inscrição.”
- Não inventar link de pagamento.
- Não inventar chave Pix.
- O link de pagamento deve vir somente de credenciais_canais["danca_circular"]["link_pagamento"].
- Se o link estiver vazio, dizer que a equipe vai confirmar o link certinho.
- Não dizer “últimas vagas” sem dado real.
- Pode dizer: “o grupo será pequeno, com apenas 10 mulheres”.
- Não dizer que a vaga está garantida antes de confirmação.
- Não pressionar a pessoa.
- Não mandar textos longos fora da sequência planejada.
- Não chamar Meta diretamente.
- Não configurar CAPI live.
- Não fazer pergunta intermediária de qualificação.
- Não esperar o lead perguntar sobre valor, roupa ou experiência com dança; a sequência já deve explicar.
- Quando houver avanço de etapa, aplicar a tag CRM adequada. O backend decide a CAPI.
- A tag “DC - Inscrição paga” é manual e só deve ser aplicada pelo operador/humano quando o Pix for confirmado.
- Se a pessoa disser que pagou, o agente não deve confirmar pagamento; deve apenas dizer que a equipe vai confirmar certinho.
- Purchase nunca deve ser disparado automaticamente pelo agente.

Tags:
- Pediu informações/iniciou conversa: DC - Pediu informações
- Respondeu QUERO MINHA VAGA ou equivalente: DC - Pediu vaga
- Enviou nome/dados para reserva: DC - Inscrição confirmada
- Pagamento confirmado por humano: DC - Inscrição paga
- Precisa de humano: Atendimento humano
- DC - Interesse confirmado pode existir, mas não deve ser aplicada automaticamente só pelo envio da sequência.

Respostas proibidas:
- “Você será curada.”
- “A cura é garantida.”
- “Últimas vagas.”
- “Essa vivência vai transformar sua vida.”
- “O pagamento é por este link: [inventado].”
- “Pagamento confirmado” sem confirmação humana.
- qualquer endereço inventado.
- textos longos demais fora da sequência.
- pressão de venda."""

DANCA_CONFIG: dict[str, Any] = {
    "nome_evento": "Dança Circular para Mulheres",
    "data": "08/08",
    "horario": "17h",
    "vagas_total": 10,
    "valor": 70,
    "moeda": "BRL",
    "cidade": "Caxias do Sul",
    "publico": "mulheres",
    "link_pagamento": None,
    "local": None,
    "orientacoes_participante": [
        "Vir com roupa confortável.",
        "Se quiser, usar uma saia para dançar.",
        "Pode se arrumar, se maquiar ou vir de um jeito que faça ela se sentir bem.",
        "O mais importante é vir à vontade.",
        "Não precisa saber dançar.",
        "Não precisa ter experiência com dança.",
    ],
}

TAGS: list[dict[str, Any]] = [
    {
        "nome": "DC - Pediu informações",
        "tipo": "conversao",
        "cor": "#0f766e",
        "disparar_conversao_ads": True,
        "event_name": "Contact",
        "default_value": None,
        "instrucao_ia": "Aplicar quando a pessoa pedir informações sobre a Dança Circular.",
    },
    {
        "nome": "DC - Interesse confirmado",
        "tipo": "conversao",
        "cor": "#2563eb",
        "disparar_conversao_ads": True,
        "event_name": "Lead",
        "default_value": None,
        "instrucao_ia": "Manter disponível, mas não aplicar automaticamente no fluxo padrão. Usar apenas se a pessoa demonstrar interesse positivo antes de pedir vaga.",
    },
    {
        "nome": "DC - Pediu vaga",
        "tipo": "conversao",
        "cor": "#7c3aed",
        "disparar_conversao_ads": True,
        "event_name": "Lead",
        "default_value": None,
        "instrucao_ia": "Aplicar quando quiser reservar, pedir vaga ou mandar QUERO MINHA VAGA.",
    },
    {
        "nome": "DC - Inscrição confirmada",
        "tipo": "conversao",
        "cor": "#16a34a",
        "disparar_conversao_ads": True,
        "event_name": "CompleteRegistration",
        "default_value": 70,
        "instrucao_ia": "Aplicar quando a pessoa enviar os dados necessários e confirmar a reserva.",
    },
    {
        "nome": "DC - Inscrição paga",
        "tipo": "conversao",
        "cor": "#15803d",
        "disparar_conversao_ads": True,
        "event_name": "Purchase",
        "default_value": 70,
        "instrucao_ia": "Aplicar manualmente quando o operador confirmar que a inscrição foi paga via Pix.",
    },
    {
        "nome": "Atendimento humano",
        "tipo": "operacional",
        "cor": "#dc2626",
        "disparar_conversao_ads": False,
        "event_name": None,
        "default_value": None,
        "instrucao_ia": "Aplicar quando precisar de atendimento humano.",
        "acao_transferir_humano": True,
    },
]


def changed(current: Any, expected: Any) -> bool:
    return current != expected


async def get_one(session, model, *conditions):
    result = await session.execute(select(model).where(*conditions))
    return result.scalars().first()


async def main() -> None:
    await ensure_meta_capi_tables(engine)
    counters: dict[str, int] = {}

    async with AsyncSessionLocal() as session:
        empresa = await get_one(session, Empresa, Empresa.id == EMPRESA_ID)
        if not empresa or empresa.nome_empresa != EMPRESA_NOME:
            raise RuntimeError("Empresa Rosangela Ferrioli Terapia não encontrada pelo ID esperado.")

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
        if credenciais.get("danca_circular") != DANCA_CONFIG:
            credenciais["danca_circular"] = copy.deepcopy(DANCA_CONFIG)
            counters["credenciais_danca_circular_atualizadas"] = 1

        sequencias = credenciais.get("sequencias_whatsapp")
        if not isinstance(sequencias, dict):
            sequencias = {}
        if sequencias.get("danca_circular") != SEQUENCIA_WHATSAPP_CONFIG:
            sequencias["danca_circular"] = copy.deepcopy(SEQUENCIA_WHATSAPP_CONFIG)
            credenciais["sequencias_whatsapp"] = sequencias
            counters["sequencia_whatsapp_danca_circular_atualizada"] = 1

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
                "acao_transferir_humano": bool(tag_def.get("acao_transferir_humano", False)),
                "instrucao_ia": tag_def.get("instrucao_ia"),
            }.items():
                if changed(getattr(tag, field), value):
                    setattr(tag, field, value)
                    counters["tags_atualizadas"] = counters.get("tags_atualizadas", 0) + 1

            if tag.nome == "DC - Inscrição paga":
                paid_tag_id = tag.id

            tags_resultado.append({"id": str(tag.id), "nome": tag.nome, "tipo": tag.tipo})

            if not tag_def["event_name"]:
                continue

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
                "empresa": {"id": EMPRESA_ID, "nome": EMPRESA_NOME},
                "danca_circular": DANCA_CONFIG,
                "sequencia_whatsapp": SEQUENCIA_WHATSAPP_CONFIG,
                "tags": tags_resultado,
                "configs_capi": configs_resultado,
                "contadores": counters,
                "seguranca": [
                    "link_pagamento permanece null.",
                    "local permanece null.",
                    "Todas as configs CAPI gerenciadas foram gravadas em dry_run.",
                    "DC - Inscrição paga é manual e é a única tag com Purchase.",
                    "Nenhuma chamada OpenAI, WhatsApp ou Meta real foi feita por este script.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    asyncio.run(main())

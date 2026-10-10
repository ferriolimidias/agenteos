from __future__ import annotations

import os

import asyncio
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm.attributes import flag_modified

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.core.security import get_password_hash, is_bcrypt_hash, verify_password
from db.database import AsyncSessionLocal
from db.models import (
    ADMIN_EMPRESA_ROLE,
    AgendaConfiguracao,
    Agente,
    Conhecimento,
    ConhecimentoRAG,
    CRMEtapa,
    CRMFunil,
    Empresa,
    Especialista,
    TagCRM,
    TagGroup,
    Usuario,
    is_root_admin_email,
    is_super_admin_role,
    normalize_user_email,
)

# Este script configura um tenant de exemplo/teste.
# Não representa regra global da plataforma.
# Não deve ser importado pelo runtime principal.

EMPRESA_NOME = "JJClean"
TEST_USER_EMAIL = "jjclean.teste@agenteos.local"
TEST_USER_PASSWORD = os.environ["AGENTEOS_TEST_TENANT_PASSWORD"]
TEST_USER_NAME = "JJClean Teste"


CONHECIMENTO_TITULO = "Atendimento e orçamento JJClean"
CONHECIMENTO_TITULO_LEGADO = "Regras de orçamento JJClean"
CONHECIMENTO_CONTEUDO = """A JJClean realiza higienização e limpeza de estofados para residências e empresas. O atendimento deve ser cordial, direto e confiável.

Serviços:
* Higienização de sofás.
* Higienização de colchões.
* Higienização de cadeiras.
* Combinações de itens.

Como orçar:
* Sofá: perguntar o tamanho aproximado em metros.
* Colchão: perguntar se é solteiro, casal, queen ou king.
* Cadeiras: perguntar a quantidade.
* Foto: solicitar quando possível para ajudar na avaliação visual.

Tabela:
Sofás:
* Até 1,80 m: R$ 180.
* Até 2,30 m: R$ 200.
* Até 2,80 m: R$ 250.
* Até 3,00 m: R$ 300.
* Acima de 3,00 m: solicitar confirmação do especialista.

Colchões:
* Solteiro: R$ 80.
* Casal: R$ 100.
* Queen: R$ 120.
* King: R$ 150.

Cadeiras:
* R$ 15 por cadeira.

Aviso obrigatório:
Todo orçamento é prévio e aproximado. O valor final pode variar após avaliação do especialista no local.

Agendamento:
Se o cliente aceitar o orçamento, coletar:
* nome;
* endereço ou bairro;
* melhor dia;
* melhor turno;
* telefone;
* observações sobre o estofado.

A visita fica sujeita à confirmação da equipe.
"""


ORCAMENTO_CONFIG: dict[str, Any] = {
    "enabled": True,
    "version": 1,
    "currency": "R$",
    "disclaimer": (
        "Esse é um orçamento prévio e aproximado, calculado com base nas informações que você me passou. "
        "O valor final pode variar após a avaliação do especialista no local."
    ),
    "image_fallback_message": (
        "Recebi a foto, obrigado. Ela ajuda bastante na avaliação, mas para calcular o orçamento preciso também "
        "do tamanho aproximado do sofá em metros. Você sabe me dizer a medida dele?"
    ),
    "greeting_response": (
        "Olá! Tudo bem? Eu sou o assistente da JJClean. Posso te ajudar com orçamento para higienização de sofá, "
        "colchão ou cadeiras. Me conta o que você precisa limpar?"
    ),
    "human_handoff_message": (
        "Claro. Vou encaminhar seu atendimento para uma pessoa da equipe da JJClean acompanhar com você."
    ),
    "messages": {
        "missing_image_details": (
            "Recebi a foto, obrigado. Ela ajuda bastante na avaliação, mas para calcular o orçamento preciso também "
            "do tamanho aproximado do sofá em metros. Você sabe me dizer a medida dele?"
        ),
        "missing_by_field": {
            "tamanho do sofá em metros": (
                "Claro, posso te ajudar com uma estimativa. Para sofá, o orçamento depende do tamanho aproximado em metros. "
                "Você sabe me dizer a medida dele? Se puder, também pode mandar uma foto para ajudar na avaliação."
            ),
            "tipo/tamanho do colchão": (
                "Claro, posso te ajudar com uma estimativa. Para colchão, preciso saber o tamanho: solteiro, casal, queen ou king."
            ),
            "quantidade de cadeiras": "Claro, posso te ajudar com uma estimativa. Para cadeiras, me diga por favor a quantidade.",
        },
        "missing_sofa_measure": (
            "Claro, posso te ajudar com uma estimativa. Para sofá, o orçamento depende do tamanho aproximado em metros. "
            "Você sabe me dizer a medida dele? Se puder, também pode mandar uma foto para ajudar na avaliação."
        ),
        "missing_image_sofa_measure": (
            "Recebi a foto, obrigado. Ela ajuda bastante na avaliação, mas para calcular o orçamento preciso também "
            "do tamanho aproximado do sofá em metros. Você sabe me dizer a medida dele?"
        ),
        "schedule_request": (
            "Perfeito. Para solicitar o agendamento da visita, me envie por favor: nome, endereço ou bairro, melhor dia "
            "e melhor turno para atendimento. A equipe confirma a disponibilidade antes de fechar o horário."
        ),
        "budget_next_step": "Quer que eu te ajude a solicitar o agendamento da visita?",
        "critical_condition": (
            "Se o item estiver muito sujo, manchado, com urina, mofo ou em estado crítico, o valor precisa ser "
            "confirmado pelo especialista no local."
        ),
    },
    "scheduling_required_fields": [
        "nome",
        "endereco/bairro",
        "melhor dia",
        "melhor turno",
        "telefone",
        "observacoes sobre o estofado",
    ],
    "services": {
        "sofa": {
            "label": "Sofá",
            "response_label": "um sofá",
            "aliases": ["sofá", "sofa", "estofado"],
            "pricing_type": "tier_by_measure",
            "missing_field": "tamanho do sofá em metros",
            "tiers": [
                {"max": 1.8, "price": 180, "label": "Sofá até 1,80 m"},
                {"max": 2.3, "price": 200, "label": "Sofá até 2,30 m"},
                {"max": 2.8, "price": 250, "label": "Sofá até 2,80 m"},
                {"max": 3.0, "price": 300, "label": "Sofá até 3,00 m"},
            ],
        },
        "colchao": {
            "label": "Colchão",
            "aliases": ["colchão", "colchao"],
            "pricing_type": "variant",
            "missing_field": "tipo/tamanho do colchão",
            "variants": {
                "solteiro": {"label": "Colchão solteiro", "aliases": ["solteiro"], "price": 80},
                "casal": {"label": "Colchão casal", "aliases": ["casal"], "price": 100},
                "queen": {"label": "Colchão queen", "aliases": ["queen"], "price": 120},
                "king": {"label": "Colchão king", "aliases": ["king"], "price": 150},
            },
        },
        "cadeira": {
            "label": "Cadeira",
            "response_label_plural": "cadeiras",
            "aliases": ["cadeira", "cadeiras"],
            "pricing_type": "unit",
            "missing_field": "quantidade de cadeiras",
            "unit_price": 15,
        },
    },
}


ESPECIALISTAS = [
    ("atendimento_principal", "Atendimento principal", "Atender de forma profissional, simples, cordial, objetiva e confiável."),
    ("orcamento", "Orçamento", "Calcular orçamento prévio usando regras configuradas do tenant e nunca prometer valor final."),
    ("agendamento", "Agendamento", "Coletar dados necessários para solicitação de visita e informar que a equipe confirma."),
    ("transferencia", "Suporte/Transferência humana", "Encaminhar para atendimento humano quando necessário."),
    ("rag", "RAG/Base de conhecimento", "Consultar e explicar regras e informações cadastradas na base da empresa."),
]

TAGS = [
    "Novo lead",
    "Orçamento solicitado",
    "Orçamento enviado",
    "Aguardando aceite",
    "Agendamento solicitado",
    "Visita a confirmar",
    "Atendimento humano",
    "Serviço concluído",
]

ETAPAS = [
    "Novo contato",
    "Coleta de informações",
    "Orçamento enviado",
    "Aguardando aceite",
    "Agendamento solicitado",
    "Visita a confirmar",
    "Serviço concluído",
]


def changed(current: Any, expected: Any) -> bool:
    return current != expected


async def get_one(session, model, *conditions):
    result = await session.execute(select(model).where(*conditions))
    return result.scalars().first()


async def main() -> None:
    counters: Counter[str] = Counter()
    async with AsyncSessionLocal() as session:
        empresa = await get_one(session, Empresa, Empresa.nome_empresa == EMPRESA_NOME)
        if not empresa:
            empresa = Empresa(nome_empresa=EMPRESA_NOME)
            session.add(empresa)
            await session.flush()
            counters["empresas_criadas"] += 1
        else:
            counters["empresas_existentes"] += 1

        updates = {
            "area_atuacao": "Higienização e limpeza de estofados",
            "informacoes_adicionais": (
                "Higienização de sofás, colchões e cadeiras para residências e empresas. "
                "WhatsApp principal: (21) 97318-1169. Instagram: @jjlimpezaprofissional."
            ),
            "ia_personalidade": (
                "Atendimento profissional, cordial, simples, objetivo e confiável. O agente deve conduzir a conversa "
                "com naturalidade, acolher o pedido do cliente, fazer perguntas curtas, explicar o orçamento com clareza "
                "e encaminhar para agendamento quando houver interesse."
            ),
            "ia_regras_negocio": (
                "Objetivo: Atender clientes interessados em higienização de estofados, identificar o serviço desejado, "
                "coletar informações necessárias, passar orçamento prévio aproximado e solicitar agendamento de visita "
                "quando o cliente aceitar.\n"
                "- Sempre cumprimentar de forma natural no primeiro contato.\n"
                "- Apresentar-se como assistente da JJClean quando fizer sentido.\n"
                "- Perguntar se o cliente precisa de higienização de sofá, colchão, cadeiras ou combinação.\n"
                "- Para sofá, pedir medida aproximada em metros.\n"
                "- Para colchão, pedir tipo/tamanho: solteiro, casal, queen ou king.\n"
                "- Para cadeiras, pedir quantidade.\n"
                "- Pedir foto quando ajudar na avaliação.\n"
                "- Se receber foto, agradecer e explicar que a foto ajuda, mas ainda precisa de medida/tipo/quantidade.\n"
                "- Nunca passar valor final como definitivo.\n"
                "- Sempre dizer que é orçamento prévio/aproximado.\n"
                "- Se o item estiver muito sujo, manchado, com urina, mofo ou estado crítico, informar que precisa confirmação do especialista.\n"
                "- Se o cliente aceitar, coletar nome, endereço/bairro, melhor dia, melhor turno e telefone.\n"
                "- Informar que a equipe confirma a disponibilidade antes de fechar o horário.\n"
                "- Se o cliente pedir humano, encaminhar de forma cordial.\n"
                "- Não usar linguagem técnica do sistema.\n"
                "- Não mencionar componentes internos, JSON, provider ou modelo."
            ),
            "nome_agente": "Assistente JJClean",
            "mensagem_saudacao": (
                "Olá! Tudo bem? Eu sou o assistente da JJClean. Posso te ajudar com orçamento e agendamento para "
                "higienização de sofás, colchões e cadeiras."
            ),
            "modelo_ia": "gpt-4o-mini",
            "modelo_roteador": "gpt-4o-mini",
            "telefone_notificacao": "(21) 97318-1169",
        }
        updated_empresa = False
        for field, value in updates.items():
            if changed(getattr(empresa, field), value):
                setattr(empresa, field, value)
                updated_empresa = True

        credenciais = dict(empresa.credenciais_canais or {})
        tenant_config = {
            "whatsapp_principal": "(21) 97318-1169",
            "instagram": "@jjlimpezaprofissional",
            "segmento": "Higienização e limpeza de estofados",
        }
        image_analysis_config = {
            "enabled": False,
            "vision_provider": "openai",
            "vision_model": "gpt-4o-mini",
            "max_image_size_mb": 8,
            "require_human_confirmation": True,
            "fallback_message": "Recebi a imagem. Para te orientar melhor, me diga também alguns detalhes importantes.",
        }
        if credenciais.get("orcamento") != ORCAMENTO_CONFIG:
            credenciais["orcamento"] = ORCAMENTO_CONFIG
            updated_empresa = True
        if "image_analysis" not in credenciais:
            credenciais["image_analysis"] = image_analysis_config
            updated_empresa = True
        if credenciais.get("tenant_public_profile") != tenant_config:
            credenciais["tenant_public_profile"] = tenant_config
            updated_empresa = True
        if updated_empresa:
            empresa.credenciais_canais = credenciais
            flag_modified(empresa, "credenciais_canais")
            counters["empresas_atualizadas"] += 1

        agente = await get_one(session, Agente, Agente.empresa_id == empresa.id, Agente.nome == "Assistente JJClean")
        agente_prompt = (
            "Atue como atendente principal do tenant. Use especialistas configurados e regras de conhecimento. "
            "Valores devem ser sempre tratados como orçamento prévio/aproximado."
        )
        if not agente:
            session.add(
                Agente(
                    empresa_id=empresa.id,
                    nome="Assistente JJClean",
                    modelo_ia="gpt-4o-mini",
                    prompt_sistema=agente_prompt,
                    ativo=True,
                )
            )
            counters["agentes_criados"] += 1
        elif agente.prompt_sistema != agente_prompt or agente.modelo_ia != "gpt-4o-mini" or not agente.ativo:
            agente.prompt_sistema = agente_prompt
            agente.modelo_ia = "gpt-4o-mini"
            agente.ativo = True
            counters["agentes_atualizados"] += 1

        for key, nome, missao in ESPECIALISTAS:
            especialista = await get_one(session, Especialista, Especialista.empresa_id == empresa.id, Especialista.nome == key)
            prompt = (
                f"Especialista: {nome}. {missao} "
                "Use apenas dados configurados no tenant e base de conhecimento; não invente regras."
            )
            usar_rag = key in {"rag", "orcamento"}
            usar_agenda = key == "agendamento"
            if not especialista:
                session.add(
                    Especialista(
                        empresa_id=empresa.id,
                        nome=key,
                        descricao_missao=missao,
                        descricao_roteamento=nome,
                        prompt_sistema=prompt,
                        modelo_ia="gpt-4o-mini",
                        modelo_llm="gpt-4o-mini",
                        peso_prioridade=2 if key == "orcamento" else 1,
                        fixo_no_roteador=key == "orcamento",
                        usar_rag=usar_rag,
                        usar_agenda=usar_agenda,
                        ativo=True,
                    )
                )
                counters["especialistas_criados"] += 1
            else:
                changed_esp = False
                for field, value in {
                    "descricao_missao": missao,
                    "descricao_roteamento": nome,
                    "prompt_sistema": prompt,
                    "modelo_ia": "gpt-4o-mini",
                    "modelo_llm": "gpt-4o-mini",
                    "peso_prioridade": 2 if key == "orcamento" else 1,
                    "fixo_no_roteador": key == "orcamento",
                    "usar_rag": usar_rag,
                    "usar_agenda": usar_agenda,
                    "ativo": True,
                }.items():
                    if changed(getattr(especialista, field), value):
                        setattr(especialista, field, value)
                        changed_esp = True
                if changed_esp:
                    counters["especialistas_atualizados"] += 1

        for model in (ConhecimentoRAG, Conhecimento):
            result_conhecimento = await session.execute(
                select(model).where(
                    model.empresa_id == empresa.id,
                    model.source_name.in_([CONHECIMENTO_TITULO, CONHECIMENTO_TITULO_LEGADO]),
                )
            )
            conhecimento = result_conhecimento.scalars().first()
            conteudo = f"[{CONHECIMENTO_TITULO}] {CONHECIMENTO_CONTEUDO}"
            if not conhecimento:
                kwargs = {
                    "empresa_id": empresa.id,
                    "conteudo": conteudo,
                    "source_name": CONHECIMENTO_TITULO,
                    "source_type": "texto",
                }
                if model is ConhecimentoRAG:
                    kwargs["tipo"] = "texto"
                    kwargs["status"] = "Ativo"
                session.add(model(**kwargs))
                counters[f"{model.__tablename__}_criados"] += 1
            else:
                changed_conhecimento = False
                if conhecimento.source_name != CONHECIMENTO_TITULO:
                    conhecimento.source_name = CONHECIMENTO_TITULO
                    changed_conhecimento = True
                if conhecimento.conteudo != conteudo:
                    conhecimento.conteudo = conteudo
                    changed_conhecimento = True
                if model is ConhecimentoRAG:
                    if getattr(conhecimento, "status", None) != "Ativo":
                        conhecimento.status = "Ativo"
                        changed_conhecimento = True
                if changed_conhecimento:
                    counters[f"{model.__tablename__}_atualizados"] += 1

        grupo = await get_one(session, TagGroup, TagGroup.empresa_id == empresa.id, TagGroup.nome == "Funil JJClean")
        if not grupo:
            grupo = TagGroup(empresa_id=empresa.id, nome="Funil JJClean", cor="#2563eb", ordem=1)
            session.add(grupo)
            await session.flush()
            counters["tag_groups_criados"] += 1

        for idx, tag_nome in enumerate(TAGS, 1):
            tag = await get_one(session, TagCRM, TagCRM.empresa_id == empresa.id, TagCRM.nome == tag_nome)
            if not tag:
                session.add(
                    TagCRM(
                        empresa_id=empresa.id,
                        grupo_id=grupo.id,
                        nome=tag_nome,
                        cor="#2563eb",
                        tipo="etapa_funil" if tag_nome in TAGS[:7] else "comportamento",
                        ordem=idx,
                        ativa_no_funil=True,
                    )
                )
                counters["tags_criadas"] += 1
            else:
                tag_changed = False
                for field, value in {"grupo_id": grupo.id, "ordem": idx, "ativa_no_funil": True}.items():
                    if changed(getattr(tag, field), value):
                        setattr(tag, field, value)
                        tag_changed = True
                if tag_changed:
                    counters["tags_atualizadas"] += 1

        funil = await get_one(session, CRMFunil, CRMFunil.empresa_id == empresa.id, CRMFunil.nome == "Funil JJClean")
        if not funil:
            funil = CRMFunil(empresa_id=empresa.id, nome="Funil JJClean")
            session.add(funil)
            await session.flush()
            counters["funis_criados"] += 1

        for idx, etapa_nome in enumerate(ETAPAS, 1):
            etapa = await get_one(session, CRMEtapa, CRMEtapa.funil_id == funil.id, CRMEtapa.nome == etapa_nome)
            tipo = "atendimento" if idx == 1 else ("fechamento" if etapa_nome == "Serviço concluído" else "custom")
            if not etapa:
                session.add(CRMEtapa(funil_id=funil.id, nome=etapa_nome, tipo=tipo, ordem=idx))
                counters["etapas_criadas"] += 1
            elif etapa.ordem != idx or etapa.tipo != tipo:
                etapa.ordem = idx
                etapa.tipo = tipo
                counters["etapas_atualizadas"] += 1

        agenda = await get_one(session, AgendaConfiguracao, AgendaConfiguracao.empresa_id == empresa.id)
        dias = {
            "segunda": {"aberto": True, "inicio": "08:00", "fim": "18:00"},
            "terca": {"aberto": True, "inicio": "08:00", "fim": "18:00"},
            "quarta": {"aberto": True, "inicio": "08:00", "fim": "18:00"},
            "quinta": {"aberto": True, "inicio": "08:00", "fim": "18:00"},
            "sexta": {"aberto": True, "inicio": "08:00", "fim": "18:00"},
            "sabado": {"aberto": True, "inicio": "08:00", "fim": "12:00"},
            "domingo": {"aberto": False, "inicio": None, "fim": None},
        }
        if not agenda:
            session.add(AgendaConfiguracao(empresa_id=empresa.id, dias_funcionamento=dias, excecoes=[], duracao_slot_minutos=60))
            counters["agenda_criada"] += 1
        elif agenda.dias_funcionamento != dias or agenda.duracao_slot_minutos != 60:
            agenda.dias_funcionamento = dias
            agenda.duracao_slot_minutos = 60
            flag_modified(agenda, "dias_funcionamento")
            counters["agenda_atualizada"] += 1

        email_teste = normalize_user_email(TEST_USER_EMAIL)
        usuario = await get_one(session, Usuario, Usuario.email == email_teste)
        usuario_status = "inalterado"
        if usuario and (is_root_admin_email(usuario.email) or is_super_admin_role(usuario.role)):
            raise RuntimeError("Usuário de teste conflita com super admin; operação abortada sem alterar o usuário.")
        if usuario and usuario.empresa_id and usuario.empresa_id != empresa.id:
            raise RuntimeError(
                f"Usuário de teste já está vinculado a outra empresa ({usuario.empresa_id}); operação abortada."
            )

        if not usuario:
            usuario = Usuario(
                empresa_id=empresa.id,
                nome=TEST_USER_NAME,
                email=email_teste,
                senha_hash=get_password_hash(TEST_USER_PASSWORD),
                role=ADMIN_EMPRESA_ROLE,
                ativo=True,
            )
            session.add(usuario)
            await session.flush()
            usuario_status = "criado"
            counters["usuarios_criados"] += 1
        else:
            usuario_changed = False
            if usuario.empresa_id != empresa.id:
                usuario.empresa_id = empresa.id
                usuario_changed = True
            if usuario.nome != TEST_USER_NAME:
                usuario.nome = TEST_USER_NAME
                usuario_changed = True
            if usuario.role != ADMIN_EMPRESA_ROLE:
                usuario.role = ADMIN_EMPRESA_ROLE
                usuario_changed = True
            if not usuario.ativo:
                usuario.ativo = True
                usuario_changed = True
            senha_ok = is_bcrypt_hash(usuario.senha_hash) and verify_password(TEST_USER_PASSWORD, usuario.senha_hash)
            if not senha_ok:
                usuario.senha_hash = get_password_hash(TEST_USER_PASSWORD)
                usuario_changed = True
                counters["usuarios_senha_atualizada"] += 1
            if usuario_changed:
                usuario_status = "atualizado"
                counters["usuarios_atualizados"] += 1

        await session.commit()

        print(json.dumps(
            {
                "empresa_id": str(empresa.id),
                "usuario_teste": {
                    "status": usuario_status,
                    "id": str(usuario.id),
                    "email": email_teste,
                    "senha_temporaria": "[REDACTED]",
                    "role": ADMIN_EMPRESA_ROLE,
                    "empresa_id": str(empresa.id),
                    "aviso": "Senha temporária de teste; trocar após validação.",
                },
                "criados": {k: v for k, v in counters.items() if k.endswith("_criados") or k.endswith("_criadas") or k == "agenda_criada"},
                "atualizados": {
                    k: v
                    for k, v in counters.items()
                    if k.endswith("_atualizados")
                    or k.endswith("_atualizadas")
                    or k == "agenda_atualizada"
                    or k == "usuarios_senha_atualizada"
                },
                "observacoes": [
                    "Dados específicos da empresa foram salvos no banco e em configuração JSONB do tenant.",
                    "Nenhum envio WhatsApp, chamada OpenAI ou migration foi executado.",
                    "Usuário de teste criado/normalizado com senha bcrypt usando função existente.",
                ],
                "proximos_passos": [
                    "Validar a empresa no painel.",
                    "Trocar a senha temporária após os testes.",
                    "Conectar WhatsApp pelo fluxo normal de conexões.",
                ],
            },
            ensure_ascii=False,
            indent=2,
        ))


if __name__ == "__main__":
    asyncio.run(main())

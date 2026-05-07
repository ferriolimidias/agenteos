from __future__ import annotations

FERRAMENTAS_SISTEMA = [
    {
        "nome_exibicao": "Atualizar Nome do Lead",
        "nome_ferramenta": "tool_atualizar_nome_lead",
        "descricao_ia": "Atualiza o nome oficial do lead no CRM após confirmação explícita do cliente.",
        "schema_parametros": {
            "type": "object",
            "properties": {"novo_nome": {"type": "string", "description": "Nome confirmado pelo cliente para cadastro."}},
            "required": ["novo_nome"],
            "additionalProperties": False,
        },
    },
    {
        "nome_exibicao": "Aplicar Tag Dinâmica",
        "nome_ferramenta": "tool_aplicar_tag_dinamica",
        "descricao_ia": "Recebe o tag_id (UUID) para aplicar uma etiqueta ao lead. Obrigatório consultar o ID antes.",
        "schema_parametros": {
            "type": "object",
            "properties": {"tag_id": {"type": "string", "description": "UUID da etiqueta oficial que deve ser aplicada ao lead atual."}},
            "required": ["tag_id"],
            "additionalProperties": False,
        },
    },
    {
        "nome_exibicao": "Transferir para Humano (Pausar Bot)",
        "nome_ferramenta": "tool_transferir_para_humano",
        "descricao_ia": "Permite que o agente pause o bot por 24 horas e coloque o lead na fila de atendimento humano.",
        "schema_parametros": {
            "type": "object",
            "properties": {"motivo": {"type": "string", "description": "Resumo curto do motivo para pausar o bot e transferir para humano."}},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "nome_exibicao": "Consultar Lista de Tags",
        "nome_ferramenta": "tool_consultar_tags_empresa",
        "descricao_ia": "Retorna a lista oficial de etiquetas com NOME e ID. Use isso antes de aplicar qualquer tag.",
        "schema_parametros": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
]


ESPECIALISTAS_NATIVOS = {
    "especialista_saudacao": {
        "descricao_missao": "Agente porteiro de entrada: cumprimenta com brevidade e identifica a intenção do cliente.",
        "descricao_roteamento": "oi, olá, bom dia, boa tarde, boa noite, tudo bem, iniciar atendimento, primeira mensagem, início de conversa",
        "prompt_sistema": (
            "Você é o micro-agente PORTEIRO de saudação.\n"
            "Objetivo: responder em 1-2 frases, com cordialidade, e identificar rapidamente a intenção principal do cliente.\n"
            "Regras: não resolver o caso completo, não entrar em detalhes técnicos, não prometer ações internas.\n"
            "Validação de nome: analise o nome de perfil do WhatsApp no contexto. Se parecer estranho, incompleto, só emoji/números/caracteres, pergunte como a pessoa prefere ser chamada.\n"
            "Assim que o cliente confirmar o nome, chame OBRIGATORIAMENTE a ferramenta tool_atualizar_nome_lead antes de continuar.\n"
            "Depois de acolher, faça uma pergunta curta que ajude o roteamento para o especialista correto."
        ),
        "fixo_no_roteador": True,
    },
    "especialista_localizacao": {
        "descricao_missao": "Fornecer o endereço completo, ponto de referência e enviar o link do Google Maps (mapa) para ajudar o cliente a chegar à unidade.",
        "descricao_roteamento": "endereço, onde fica, mapa, link do maps, google maps, matriz, filial, ponto de referência, como chegar, me manda o mapa, referências do local, fica perto de onde, rua, avenida, bairro, cidade, localização novamente, endereço de novo, gps, rota, manda a localização",
        "prompt_sistema": "Você é o especialista de localização. REGRAS OBRIGATÓRIAS: 1. NUNCA peça permissão para enviar o endereço ou o link, envie imediatamente. 2. Use o PONTO DE REFERÊNCIA cadastrado como guia principal. 3. Se houver um link de mapa disponível no contexto, forneça-o. 4. NUNCA invente ou gere links falsos. Se não houver link no contexto, envie apenas o endereço em texto.",
        "fixo_no_roteador": True,
    },
    "especialista_funcionamento": {
        "descricao_missao": "Responder perguntas sobre dias e horários de atendimento.",
        "descricao_roteamento": "horário de atendimento, que horas abre, que horas fecha, dias de funcionamento, vocês abrem de sábado, abrem feriado, estão abertos hoje, expediente",
        "prompt_sistema": "Você é o especialista de funcionamento. Informe horários, dias úteis e regras de abertura com clareza.",
        "fixo_no_roteador": True,
    },
    "especialista_followup": {
        "descricao_missao": "Gerar as mensagens automáticas de retomada de conversa e de encerramento por inatividade.",
        "descricao_roteamento": "NÃO DEVE SER CHAMADO DIRETAMENTE PELO ROTEADOR. USO INTERNO DO SISTEMA DE DELAY.",
        "prompt_sistema": "Você é o especialista de engajamento da empresa. Seu tom é educado, sutil e empático. Seu objetivo é reconectar com clientes que pararam de responder ou encerrar contatos inativos de forma elegante, deixando as portas sempre abertas.",
        "fixo_no_roteador": False,
    },
    "especialista_handoff_interno": {
        "descricao_missao": "Micro-agente interno de transbordo: envia mensagem curta de transferência quando necessário.",
        "descricao_roteamento": "NÃO DEVE SER CHAMADO PELO ROTEADOR SEMÂNTICO. USO INTERNO EXCLUSIVO PARA HANDOFF.",
        "prompt_sistema": (
            "Você é o micro-agente interno de transbordo (handoff).\n"
            "Objetivo: quando a IA for pausada e ainda não houver resposta humana, gerar uma única mensagem curta e contextual de transferência.\n"
            "Se houver indício de que um humano já assumiu a conversa, não enviar mensagem."
        ),
        "fixo_no_roteador": False,
    },
}


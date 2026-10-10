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
    {
        "nome_exibicao": "Listar Etapas do Funil (CRM)",
        "nome_ferramenta": "tool_listar_etapas_funil",
        "descricao_ia": (
            "Lista todas as etapas do CRM desta empresa (nome + UUID). "
            "Use antes de mover o lead; os UUIDs são os únicos identificadores válidos de etapa."
        ),
        "schema_parametros": {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    },
    {
        "nome_exibicao": "Atualizar Etapa do Lead (CRM)",
        "nome_ferramenta": "tool_atualizar_etapa_lead",
        "descricao_ia": (
            "Move o lead para uma etapa do funil. O parâmetro etapa_id deve ser um UUID obtido de tool_listar_etapas_funil."
        ),
        "schema_parametros": {
            "type": "object",
            "properties": {
                "etapa_id": {
                    "type": "string",
                    "description": "UUID da etapa de destino (copiado da listagem oficial do funil desta empresa).",
                }
            },
            "required": ["etapa_id"],
            "additionalProperties": False,
        },
    },
    {
        "nome_exibicao": "Adicionar Tag ao Lead (CRM)",
        "nome_ferramenta": "tool_adicionar_tag_lead",
        "descricao_ia": (
            "Aplica uma etiqueta oficial ao lead. Informe tag_id (UUID) OU tag_nome (nome exato da tag). "
            "Prefira UUID após tool_consultar_tags_empresa."
        ),
        "schema_parametros": {
            "type": "object",
            "properties": {
                "tag_id": {
                    "type": "string",
                    "description": "UUID da etiqueta oficial (opcional se tag_nome for informado).",
                },
                "tag_nome": {
                    "type": "string",
                    "description": "Nome exato ou próximo da etiqueta oficial (opcional se tag_id for informado).",
                },
            },
            "required": [],
            "additionalProperties": False,
        },
    },
]


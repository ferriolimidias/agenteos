# Arquitetura Multi-Tenant de Agentes

## Diretriz multi-tenant do AgenteOS

1. O sistema nunca deve assumir regras específicas de uma empresa.
2. O tenant fornece identidade, tom, regras, conhecimento, políticas comerciais e templates.
3. O IntentRouter classifica intenção de forma genérica.
4. Especialistas executam competências genéricas.
5. Regras comerciais vêm do tenant.
6. A IA, quando habilitada, atua como camada de interpretação e redação.
7. A IA não deve inventar preço, regra, política, prazo ou promessa.
8. Se uma regra não estiver configurada, o agente deve perguntar, pedir confirmação ou encaminhar para humano.
9. O ResponseGuard valida a resposta final.
10. Tenant não pode editar prompt/config crítica; Super Admin configura.

## Hierarquia de fonte de verdade

1. Segurança da plataforma:
   - ResponseGuard
   - ToolPolicy
   - bloqueios globais

2. Configuração do tenant:
   - nome do agente
   - saudação
   - personalidade
   - regras de negócio
   - templates
   - políticas comerciais
   - image_analysis
   - BYOK/modelos

3. Especialistas do tenant:
   - orçamento
   - agenda
   - suporte
   - CRM
   - RAG
   - transferência

4. Base de conhecimento do tenant.

5. Mensagem atual do cliente.

6. IA generativa, se habilitada:
   - apenas para interpretar/redigir
   - nunca para sobrescrever fonte de verdade

## Orçamento

O especialista de orçamento é genérico. Ele pode detectar intenção de orçamento, extrair quantidades, medidas e variantes, buscar regras configuradas e calcular quando existe tabela do tenant. Ele não deve carregar serviço, preço, faixa, texto comercial ou regra de segmento como padrão global.

As regras devem vir de `Empresa.credenciais_canais["orcamento"]` ou de estrutura equivalente do tenant. Quando a regra não existe ou os dados são insuficientes, o especialista deve pedir os campos faltantes ou encaminhar para confirmação humana.

## Política de LLM

Quando houver IA configurada:

- A IA pode interpretar linguagem livre.
- A IA pode melhorar redação.
- A IA pode resumir contexto.
- A IA pode analisar imagem quando habilitado.
- A IA não pode inventar preço.
- A IA não pode ignorar regras comerciais.
- A IA não pode prometer agendamento confirmado se isso exige confirmação humana.
- A IA não pode substituir cálculos feitos por especialistas.
- A IA deve receber dados estruturados do especialista como fonte de verdade.
- A IA deve seguir regras do tenant.
- Se houver conflito entre IA e regra do tenant, regra do tenant vence.

## Onde dados de exemplo podem existir

Dados específicos de uma empresa de teste podem existir em scripts de setup, scripts de verificação do próprio tenant, testes específicos e relatórios. Eles não podem aparecer como regra fixa em `app/agents`, `app/api`, `app/services`, roteadores, providers, composers ou especialistas genéricos.

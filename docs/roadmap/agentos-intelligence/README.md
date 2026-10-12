# AgentOS — Planejamento de atualização da VPS e Intelligence (10/10/2026)

> **Planejamento incorporado à fonte oficial após A00.** O GitHub `master` contém agora a nova raiz validada da VPS. A00 (#1) foi concluída no commit `8938c55181bd21a163b255bcd128cf0e85072e5c`; a correção isolada do painel (#16) está no baseline `bd1ad3b38738a9bd2a1fae6ce0072866db0c3941`. O estado de cada implantação deve ser confirmado na Issue e no relatório da release.

## Decisão do proprietário

Em 10/10/2026, foi autorizada a **substituição integral do código antigo hospedado no GitHub** pelo estado atual validado da VPS AgentOS. O código antigo não deve ser usado como base de novas funcionalidades. **Não criar cópias de segurança, branches de arquivo, tags de arquivo, espelhos nem snapshots de preservação do repositório antigo.** Substituir integralmente a árvore e as referências antigas pelo projeto atualizado da VPS, preservando apenas o planejamento, as Issues e a operação vigente. A autorização **não** abrange reiniciar/destruir a VPS, expor segredos, apagar dados em produção ou reenviar eventos Meta/WhatsApp.

**A substituição do código remoto foi concluída na A00**, com nova raiz sem pais do projeto antigo e remoção das branches antigas. Planejamento e Issues foram preservados. GitHub atualizado e imagem implantada são evidências distintas; registrar ambos em cada entrega.

## Objetivo da evolução

Meta Ads → CTWA / Inbox → contexto comercial + JEV → módulos de atendimento combináveis → FunnelSpec e aplicação das tags atuais → Commercial Events → Meta Business Messaging → pagamento verificado → Analytics → recuperação por follow-up/campanhas WhatsApp → aprendizado automático dos prompts / recomendações de Ads.

## Backlog com entregas independentes

| Ordem / frente | Issue | Benefício utilizável |
|---|---|---|
| **PRIORIDADE 0** | [#1 A00 — Reconciliar VPS e substituir código antigo do GitHub](https://github.com/ferriolimidias/agenteos/issues/1) | Repo passa a refletir o app real; release reproduzível |
| Paralelo/observação | [#2 A01 — Evidência natural P1C](https://github.com/ferriolimidias/agenteos/issues/2) | Status operacional do sender legado |
| Posterior, autorização separada | [#3 A02 — Cutover canônico LIVE](https://github.com/ferriolimidias/agenteos/issues/3) | Sender único por tenant/evento |
| Comercial | [#4 A03 — Pagamento e receita real](https://github.com/ferriolimidias/agenteos/issues/4) | Compra, refund, receita verificáveis |
| Interface | [#5 A04 — Timeline da jornada](https://github.com/ferriolimidias/agenteos/issues/5) | Contato e histórico desde anúncio ao resultado |
| Interface | [#6 A05 — Dashboard de gargalos](https://github.com/ferriolimidias/agenteos/issues/6) | Visualizar paradas, abandono, coortes e leads |
| IA | [#7 A06 — JEV em shadow](https://github.com/ferriolimidias/agenteos/issues/7) | Diagnóstico de multi-intenções e objeções |
| IA | [#8 A07 — Prompts combináveis](https://github.com/ferriolimidias/agenteos/issues/8) | Preço + confiança + oferta na mesma resposta |
| Analítica | [#9 A08 — Qualidade de atendimento](https://github.com/ferriolimidias/agenteos/issues/9) | Dúvidas não resolvidas e objeções por versão |
| Recuperação | [#10 A09 — Follow-ups](https://github.com/ferriolimidias/agenteos/issues/10) | Métricas de reativação programada/automática |
| Recuperação | [#11 A10 — Campanhas WhatsApp](https://github.com/ferriolimidias/agenteos/issues/11) | Assistência da campanha sem apagar aquisição |
| Aprendizado | [#12 A11 — Prompt Optimizer](https://github.com/ferriolimidias/agenteos/issues/12) | Melhorias automáticas com A/B e rollback |
| Ads | [#13 A12 — Publicação existente](https://github.com/ferriolimidias/agenteos/issues/13) | Promover post IG/FB ou criar criativo novo |
| Ads | [#14 A13 — Inteligência de anúncios](https://github.com/ferriolimidias/agenteos/issues/14) | Recomendações com CPQL, objeções, custo, receita |
| Confiabilidade | [#15 A14 — Admin Saúde](https://github.com/ferriolimidias/agenteos/issues/15) | Painel de integrações, workers, outbox e alertas |

**Regra:** uma issue = uma funcionalidade acabada, utilizável sozinha no AgentOS que já opera, com testes, compatibilidade e rollback. Não criar mega-refatoração ou recursos inativos aguardando muitas outras fases.

## Documentos para continuar sem este chat

1. [MIGRACAO-VPS-PARA-GITHUB.md](MIGRACAO-VPS-PARA-GITHUB.md) — primeiro passo, substituição segura.
2. [ESTADO-2026-10-10.md](ESTADO-2026-10-10.md) — baseline reportado, ainda a confirmar na VPS.
3. [ARQUITETURA-E-ENTREGAS.md](ARQUITETURA-E-ENTREGAS.md) — decisões funcionais, JEV, tags, atendimento e aprendizado.
4. [PROTOCOLO-DE-ENTREGAS.md](PROTOCOLO-DE-ENTREGAS.md) — checklist obrigatório e disciplina de releases.

## Próxima ação

**A01 (#2) implantada:** visão operacional dos senders existentes no commit `ee527ee2e767e0e3aa2c6fb0f047fb9fa8cdd67c`, conforme [contrato de execução](../../releases/A01-sender-operations.md). API/painel somente leitura por tenant e diagnóstico de ownership/dedup exclusivamente SHADOW. O estado permanece `DEPLOYED_NOT_REAL_VALIDATED`: `NO_EVENT_OBSERVED` e entrega real `NÃO VALIDADO` são explícitos enquanto faltar evidência natural rastreada. Estado, imagens e relatório estão na [Issue #2](https://github.com/ferriolimidias/agenteos/issues/2).

A02 (#3) exige autorização separada para LIVE/enforcement. Nenhum replay histórico ou envio de teste integra A01.

**A04 (#5): jornada comercial no Inbox**, conforme [contrato de execução](../../releases/A04-inbox-commercial-journey.md). Entrega independente com dados já persistidos, sem dependência de JEV ou A05: eventos comprovados, filtros, paginação, snapshots atuais e cobertura da atribuição. Validar uma jornada real preexistente em API e browser, sem criar mensagens/conversões ou inferir transições ausentes. SHA, imagens, gates e relatório são registrados na [Issue #5](https://github.com/ferriolimidias/agenteos/issues/5).


**A04 (#5) REAL_VALIDATED:** release `4e9a85e304922184f1431753081f93acdfbd907e`, com jornada real consultada e renderizada em API/browser. Essa prova de um contato não demonstra cobertura da base inteira; A01 permanece sem entrega Meta natural validada.

**A05 (#6): dashboard de funil por coorte**, conforme [contrato de execução](../../releases/A05-cohort-funnel-dashboard.md). Agrega dados existentes com contatos únicos, estágios/transições observados, snapshots, cobertura, gargalos, filtros e drill-down para A04. Atribuição e pagamentos exigem evidência; desconhecido não vira zero. SHA/imagens/gates e relatório serão registrados na [Issue #6](https://github.com/ferriolimidias/agenteos/issues/6).

**A06 (#7): diagnóstico JEV em SHADOW e configuração segura**, conforme [contrato de execução](../../releases/A06-jev-shadow.md). Painel de inteligência independente com exemplos PT-BR sintéticos, intenções simultâneas, objeções, abstenção e sugestões hipotéticas de tags existentes. Super Admin cadastra a chave cifrada em Configurações → Integrações → JEV, define orçamento/modelo, testa e ativa SHADOW sem reinício. Sem conta/chave, o estado é NÃO CONFIGURADO e a demonstração permanece explicitamente local. Cadastro não autoriza o envio de conversas reais. Validação do provider, precisão real, custo/latência reais permanecem não medidos até testes sintéticos autorizados com uma conta configurada. SHA/imagens/gates e relatório estão na Issue #7; A07 não integra esta release.

**A07 (#8): atendimento com módulos combináveis**, conforme [contrato de execução](../../releases/A07-composable-service.md). Nove módulos com versões fixas combinam assuntos em uma resposta; a etapa comercial permanece um snapshot canônico independente. Em Agentes, a pré-visualização compara a composição com uma referência legada usando dados explicitamente sintéticos, inclusive sem chave JEV. O preview não executa tools ou ações comerciais. Texto personalizado fica local; o JEV opcional aceita somente exemplos A06 definidos pelo servidor e requer configuração existente.

O atendimento dos clientes permanece **LEGACY**. A ativação futura exige modo por UUID do tenant, autorização real independente e canais autorizados; nenhuma dessas autorizações é concedida por esta entrega ou pela interface. Primeiro turno, ResponseGuard, FunnelSpec, tools e aplicação oficial de tags permanecem preservados. O trace A07 guarda somente metadados versionados, com retenção de sete dias e limite de 500 registros por tenant. Estimativa de tokens não é uso observado; custo e comparação real permanecem desconhecidos sem receipt e faturamento. Não há migração de banco nesta release. Evidências, revisão, SHA, imagens e rollback serão registrados na Issue #8.

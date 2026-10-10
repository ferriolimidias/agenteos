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

**A01 (#2): visão operacional dos senders existentes**, conforme [contrato de execução](../../releases/A01-sender-operations.md). API/painel somente leitura por tenant, estados de recebimento/erro/incerteza, filtros, detalhes e diagnóstico de ownership/dedup exclusivamente SHADOW. O painel permanece utilizável sem canário natural; `NO_EVENT_OBSERVED` e entrega real `NÃO VALIDADO` devem permanecer explícitos enquanto faltar evidência rastreada. Estado final, SHA, imagens, testes e relatório são registrados na [Issue #2](https://github.com/ferriolimidias/agenteos/issues/2).

A02 (#3) exige autorização separada para LIVE/enforcement. Nenhum replay histórico ou envio de teste integra A01.

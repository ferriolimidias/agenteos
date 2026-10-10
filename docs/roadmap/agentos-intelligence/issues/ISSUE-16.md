# A15 | Corrigir erro React removeChild ao abrir /painel após A00

Issue: https://github.com/ferriolimidias/agenteos/issues/16
Estado no inventário: OPEN

## Sintoma relatado em 10/10/2026

Ao abrir `https://agenteos.ferriolimidias.com.br/painel`, o navegador exibiu alerta:

```text
ERRO CRÍTICO CAPTURADO NO REACT
Uncaught NotFoundError: Failed to execute 'removeChild' on 'Node':
The node to be removed is not a child of this node.
Arquivo: /assets/react-vendor-Btfli2Eq.js
Linha: 6
```

O painel aparentou ficar sem renderização normal. **Causa raiz não identificada.** Não assumir que essa falha é a mesma do contrato textual `CostsConversions` (97 passed / 1 failed nos testes frontend A00) sem evidência.

### Priorização

1. Primeiro concluir A00 #1 e substituir o GitHub obsoleto pela fonte atual verificada da VPS; não alterar UI enquanto migração está bloqueada.
2. Depois reproduzir com browser console/stack trace, build hash, hooks e rotas `/painel`; verificar DOM removal por integração externa ou React lifecycle, diferenças de JS/chunks, event handlers e versões do browser. Testar em navegação privada/controlada quando apropriado.
3. Correção mínima + testes regressão; publicar como release isolada com rollback, não misturar com JEV/Meta.

### Critérios de aceite

- [ ] `/painel` abre e navega sem NotFoundError.
- [ ] Componentes e tabelas preservados, sem alterações de backend e env.
- [ ] Build e testes frontend passam ou falhas preexistentes justificadas.
- [ ] Código e frontend ativos rastreados por Git commit/imagem.
- [ ] Testes no browser, health e rollback documentados.

**Não foi corrigido nesta issue.**

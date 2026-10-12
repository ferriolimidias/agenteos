# Identidade visual Ferrioli Mídias — UX01

UX01 consolida a estrutura visual do AgentOS sobre a A06 (baseline `44bf55f2cee3d661ea21508ca15c13f2500ebc23`). Issues #17 e #18 definem a direção; UX02 mantém a responsabilidade sobre reorganização profunda da navegação.

A interface usa estrutura grafite/petróleo e conteúdo claro cinza-azulado, com bronze apenas em indicadores e detalhes. Superfícies elevadas, superfícies de seção e áreas internas têm tratamentos distintos. Não há tema escuro, neon, glow ou gradiente decorativo. Os valores de cor são decisões desta implementação, não uma paleta oficial previamente publicada.

## Fonte do sistema

`frontend/src/styles/design-tokens.css` define cores, estados, escala de espaços, tipografia, raios e sombras. `utils/designTokens.js` fornece referências às mesmas variáveis para consumidores JavaScript. `styles/design-system.css` e `components/ui.jsx` implementam cabeçalhos, seções, métricas, botões, badges, campos, tabelas, abas e estados. `styles/shell.css` organiza sidebar, cabeçalho, conteúdo e navegação móvel.

| Uso | Token |
| --- | --- |
| Canvas do aplicativo | `--fm-canvas` |
| Estrutura da navegação | `--fm-sidebar` |
| Seção / cartão / área interna | `--fm-surface` / `--fm-raised` / `--fm-inset` |
| Texto / texto secundário | `--fm-text` / `--fm-muted` |
| Ação principal / foco | `--fm-primary` / `--fm-focus` |
| Detalhe de identidade | `--fm-bronze` |
| Informação / sucesso / alerta / erro | `--fm-status-*` |

Nomes e logos configurados continuam tendo precedência. O sistema visual não substitui a identidade do cliente. Não são carregadas fontes externas; a tipografia usa a pilha local definida pelo token.

## Aplicação e compatibilidade

Os shells Super Admin e Tenant mantêm destinos, ordem de menus, permissões e contexto de impersonation. O drawer móvel inclui botão nomeado, fechamento com Escape e retorno de foco. O Inbox preserva sua área flexível limitada ao viewport, com acesso móvel à navegação.

Cinco pilotos: Empresas, Dashboard do cliente, Funil e gargalos, Inteligência SHADOW e Integração JEV. O Inbox é também validado em desktop/mobile. CSS de pilotos permanece escopado. Telas existentes continuam utilizando os componentes compartilhados sem trocar requests, callbacks ou regras comerciais.

A06 mantém a credencial somente escrita e protegida; a tela apenas recebe apresentação nova. A05 mantém evidências, filtros e cálculos; A04 mantém jornada e controles; A01 mantém contratos de conversão; Issue #16 mantém um único root React.

## Verificação e limites

O contrato executável é `docs/releases/UX01-ferrioli-visual.md`. A release exige testes frontend, baselines protegidas, regressões focadas de WhatsApp/autorização, comparação com os mesmos dados sintéticos nos assets A06/candidata e revisão independente. Testes de navegador interceptam as APIs: não enviam mensagens, tags, eventos ou consultas a providers.

Contraste de tokens, foco, navegação por teclado, labels, responsividade e reduced-motion são verificados no escopo alterado. Isso não equivale a certificação de acessibilidade de todas as telas legadas. A release modifica somente o frontend; backend, banco e serviços de integração são preservados. Evidências, imagens, rollback e conclusão ficam no relatório final da UX01 em `reports/`.

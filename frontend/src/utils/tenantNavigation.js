const item = (id, label, to, icon, aliases = []) => ({ id, label, to, icon, aliases });

// Customer navigation is task-oriented. Internal AI/Meta tools remain reachable
// only from the Super Admin area; this map is presentation, not authorization.
export const TENANT_NAVIGATION_GROUPS = [
  { id: "conversations", label: "Conversas", items: [
    item("inbox", "Caixa de entrada", "/painel/inbox", "message", ["/painel", "/painel/chat", "/painel/conversas"]),
    item("transfers", "Atendimento humano", "/painel/transferencias", "headphones"),
  ] },
  { id: "customers", label: "Clientes", items: [
    item("crm", "CRM", "/painel/crm", "users"),
    item("funnel", "Funil comercial", "/painel/funil", "chart"),
    item("tags", "Tags e organização", "/painel/tags", "tags"),
  ] },
  { id: "schedule", label: "Agenda", items: [
    item("agenda", "Agendamentos", "/painel/agenda", "calendar"),
  ] },
  { id: "campaigns", label: "Campanhas", items: [
    item("traffic", "Anúncios Meta", "/painel/trafego/meta", "chart"),
    item("instagram", "Automação Instagram", "/painel/instagram", "instagram"),
    item("campaigns", "Mensagens e campanhas", "/painel/campanhas", "campaign"),
    item("followups", "Follow-ups", "/painel/followups", "timer"),
    item("templates", "Modelos de mensagem", "/painel/campanhas/templates", "file", ["/painel/templates-whatsapp"]),
    item("audiences", "Públicos", "/painel/campanhas/publicos", "users"),
  ] },
  { id: "results", label: "Resultados", items: [
    item("dashboard", "Visão geral", "/painel/resultados", "dashboard", ["/painel/resultados/resumo", "/painel/dashboard"]),
    item("quality", "Atendimento", "/painel/qualidade", "chart"),
    item("followup-analytics", "Resultados de follow-ups", "/painel/followups/impacto", "chart"),
    item("campaign-results", "Resultados de campanhas", "/painel/campanhas/resultados", "chart"),
    item("costs", "Custos de campanhas", "/painel/campanhas/resultados/custos", "costs"),
    item("campaign-conversions", "Conversões comprovadas", "/painel/campanhas/resultados/conversoes", "chart"),
  ] },
  { id: "settings", label: "Configurações", items: [
    item("connections", "Conexão WhatsApp", "/painel/conexoes", "message"),
    item("knowledge", "Conteúdo de atendimento", "/painel/rag", "book"),
    item("account", "Dados da empresa", "/painel/unidades", "building"),
  ] },
];

export const REVIEWER_NAVIGATION_GROUPS = [{ id: "reviewer", label: "Demonstração Meta", items: [
  item("connections", "Conexões WhatsApp", "/painel/conexoes", "message"),
  item("tags", "Gestão de Tags", "/painel/tags", "tags"),
] }];

export function getTenantNavigationGroups(role) {
  if (role === "meta_app_reviewer") return REVIEWER_NAVIGATION_GROUPS;
  return TENANT_NAVIGATION_GROUPS;
}

export function normalizeTenantPath(pathname) {
  return typeof pathname === "string" ? pathname.split(/[?#]/)[0].replace(/\/+$/, "") || "/" : "/";
}

export function resolveTenantNavigation(pathname, role) {
  const path = normalizeTenantPath(pathname);
  const matches = getTenantNavigationGroups(role).flatMap((group) => group.items.flatMap((entry) =>
    [entry.to, ...entry.aliases].filter((target) => path === target || (
      role !== "meta_app_reviewer" && target.startsWith("/painel/campanhas") && path.startsWith(`${target}/`)
    )).map((target) => ({ group, item: entry, specificity: target.length }))));
  const match = matches.sort((a, b) => b.specificity - a.specificity)[0];
  if (!match) return null;
  let detail = null;
  if (match.item.id === "templates" && path.startsWith(`${match.item.to}/`)) {
    detail = path === `${match.item.to}/novo` ? "Novo modelo" : "Detalhes do modelo";
  } else if (match.item.id === "campaigns" && path.startsWith(`${match.item.to}/`)) {
    detail = path === `${match.item.to}/nova` ? "Nova campanha" : "Detalhes da campanha";
  }
  return { group: match.group, item: match.item, detail };
}

export function isTenantInboxPath(pathname) {
  return ["/painel", "/painel/chat", "/painel/inbox", "/painel/conversas"].includes(normalizeTenantPath(pathname));
}

export function navigationPreferenceKey(empresaId, role) {
  // Scope a visual preference namespace, never a token or authentication decision.
  return `agenteos:tenant-navigation:v1:${encodeURIComponent(String(empresaId ?? "unassigned"))}:${encodeURIComponent(String(role || "tenant"))}`;
}

export function sanitizeCollapsedGroups(value, groups) {
  if (!Array.isArray(value)) return [];
  const valid = new Set(groups.map((group) => group.id));
  return [...new Set(value.filter((id) => typeof id === "string" && valid.has(id)))];
}

export function readCollapsedGroups(storage, key, groups) {
  const defaults = groups.filter((group) => !["conversations", "reviewer"].includes(group.id)).map((group) => group.id);
  try {
    const raw = storage?.getItem(key);
    if (!raw) return defaults;
    const value = JSON.parse(raw);
    if (value?.version !== 1 || !Array.isArray(value.collapsed)) return defaults;
    return sanitizeCollapsedGroups(value.collapsed, groups);
  } catch {
    return defaults;
  }
}

export function saveCollapsedGroups(storage, key, collapsed, groups) {
  try {
    storage?.setItem(key, JSON.stringify({ version: 1, collapsed: sanitizeCollapsedGroups(collapsed, groups) }));
  } catch {
    // Storage can be disabled/full. Navigation remains functional in memory.
  }
}

export function visibleCollapsedGroups(collapsed, activeGroupId, manualCollapse, visitKey) {
  return collapsed.filter((id) => id !== activeGroupId || (
    manualCollapse?.groupId === id && manualCollapse?.visitKey === visitKey
  ));
}

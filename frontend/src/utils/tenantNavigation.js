const item = (id, label, to, icon, aliases = []) => ({ id, label, to, icon, aliases });

// Presentation only. App.jsx and the existing role checks own route/access behavior.
export const TENANT_NAVIGATION_GROUPS = [
  { id: "overview", label: "Visão geral e resultados", items: [
    item("dashboard", "Dashboard", "/painel", "dashboard"),
    item("funnel", "Funil e gargalos", "/painel/funil", "chart"),
    item("results", "Resultados de campanhas", "/painel/campanhas/resultados", "chart"),
    item("costs", "Custos de campanhas", "/painel/campanhas/resultados/custos", "costs"),
    item("campaign-conversions", "Conversões de campanhas", "/painel/campanhas/resultados/conversoes", "chart"),
  ] },
  { id: "attendance", label: "Atendimento e clientes", items: [
    item("inbox", "Inbox WhatsApp", "/painel/chat", "message", ["/painel/inbox", "/painel/conversas"]),
    item("crm", "Leads / CRM", "/painel/crm", "users"),
    item("agenda", "Agenda", "/painel/agenda", "calendar"),
    item("transfers", "Atendimento Humano", "/painel/transferencias", "headphones"),
    item("tags", "Tags", "/painel/tags", "tags"),
  ] },
  { id: "acquisition", label: "Aquisição e tráfego", items: [
    item("traffic", "Tráfego Meta", "/painel/trafego/meta", "chart"),
    item("meta-conversions", "Conversões Meta", "/painel/trafego/meta/conversoes", "chart"),
    item("instagram", "Instagram", "/painel/instagram", "instagram"),
  ] },
  { id: "automation", label: "Automações e inteligência", items: [
    item("intelligence", "Inteligência SHADOW", "/painel/inteligencia", "brain"),
    item("agent", "Agente de Atendimento", "/painel/agentes", "brain"),
    item("followups", "Follow-ups", "/painel/followups", "timer"),
    item("campaigns", "Campanhas", "/painel/campanhas", "campaign"),
    item("templates", "Templates de campanhas", "/painel/campanhas/templates", "file", ["/painel/templates-whatsapp"]),
    item("audiences", "Públicos de campanhas", "/painel/campanhas/publicos", "users"),
    item("simulator", "Simulação simples", "/painel/simulador", "chart"),
  ] },
  { id: "settings", label: "Configurações e integrações", items: [
    item("connections", "Conexão WhatsApp", "/painel/conexoes", "message"),
    item("knowledge", "Base de Conhecimento", "/painel/rag", "book"),
    item("account", "Configurações da Conta", "/painel/unidades", "building"),
    item("integrations", "Integrações técnicas", "/painel/integracoes", "settings"),
    item("conversion-settings", "Configurações de conversão", "/painel/conversoes", "settings"),
  ] },
];

export const REVIEWER_NAVIGATION_GROUPS = [{ id: "reviewer", label: "Demonstração Meta", items: [
  item("connections", "Conexões WhatsApp", "/painel/conexoes", "message"),
  item("tags", "Gestão de Tags", "/painel/tags", "tags"),
] }];

export function getTenantNavigationGroups(role) {
  return role === "meta_app_reviewer" ? REVIEWER_NAVIGATION_GROUPS : TENANT_NAVIGATION_GROUPS;
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
    detail = path === `${match.item.to}/novo` ? "Novo template" : "Detalhes do template";
  } else if (match.item.id === "campaigns" && path.startsWith(`${match.item.to}/`)) {
    detail = path === `${match.item.to}/nova` ? "Nova campanha" : "Detalhes da campanha";
  }
  return { group: match.group, item: match.item, detail };
}

export function isTenantInboxPath(pathname) {
  return ["/painel/chat", "/painel/inbox", "/painel/conversas"].includes(normalizeTenantPath(pathname));
}

export function navigationPreferenceKey(empresaId, role) {
  // Scope is a visual preference namespace, never a token or authentication decision.
  return `agenteos:tenant-navigation:v1:${encodeURIComponent(String(empresaId ?? "unassigned"))}:${encodeURIComponent(String(role || "tenant"))}`;
}

export function sanitizeCollapsedGroups(value, groups) {
  if (!Array.isArray(value)) return [];
  const valid = new Set(groups.map((group) => group.id));
  return [...new Set(value.filter((id) => typeof id === "string" && valid.has(id)))];
}

export function readCollapsedGroups(storage, key, groups) {
  const defaults = groups.filter((group) => !["overview", "reviewer"].includes(group.id)).map((group) => group.id);
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

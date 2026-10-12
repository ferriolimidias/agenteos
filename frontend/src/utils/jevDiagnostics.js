export const JEV_INTENTS = {
  price: "Preço", questions: "Dúvidas", conditions: "Condições", objection: "Objeção",
  trust: "Confiança", closing: "Fechamento", human: "Atendimento humano", support: "Suporte",
  availability: "Disponibilidade", booking: "Agendamento",
};
export const JEV_STATUSES = {
  demo: "Demonstração local", completed: "Provider: análise concluída",
  not_configured: "Provider não configurado", privacy_blocked: "Autorização externa ausente",
  budget_exceeded: "Orçamento indisponível", provider_error: "Falha do provider",
  ledger_unavailable: "Ledger indisponível", disabled: "Diagnóstico desativado",
  not_evaluated: "Não avaliado", abstained: "Abstenção em análise avaliada",
  concurrency_limited: "Limite de concorrência", in_progress: "Análise em andamento",
};
const LABELS = {
  ...JEV_INTENTS, discovery: "Descoberta", qualification: "Qualificação", negotiation: "Negociação",
  decision: "Decisão", unknown: "Desconhecido", price_objection: "Objeção de preço",
  price: "Preço", timing: "Momento", payment: "Pagamento", credibility: "Credibilidade",
  interest: "Interesse", readiness: "Prontidão", risk: "Risco", information: "Informação",
  availability: "Disponibilidade", objection_resolution: "Tratamento de objeção",
  human_request: "Atendimento humano", consideration: "Consideração",
  timing_objection: "Objeção de momento", trust_objection: "Objeção de confiança", fit_objection: "Objeção de adequação",
  unanswered_questions: "Dúvidas sem resposta", interruption_hypothesis: "Hipótese de interrupção",
};
export function jevLabel(value) {
  const key = typeof value === "string" ? value : value?.key;
  return LABELS[key] || (typeof key === "string" && key.length <= 80 ? key : "Desconhecido");
}
export function jevNumber(value, suffix = "") {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? `${new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 6 }).format(value)}${suffix}` : "Desconhecido";
}
export function jevConfidence(value) {
  return typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 1
    ? `${Math.round(value * 100)}%` : "Confiança desconhecida";
}
export function jevSignal(value) {
  if (value === true) return "Sim";
  if (value === false) return "Não";
  return typeof value === "number" ? jevConfidence(value) : "Desconhecido";
}
export function jevMetric(run, key, suffix = "") {
  return run?.status === "demo" ? "Não medido no provider" : jevNumber(run?.metrics?.[key], suffix);
}
export function jevError(error) {
  const status = error?.response?.status;
  if (status === 403) return "Acesso não autorizado a este tenant ou diagnóstico.";
  if (status === 404) return "Diagnóstico ou registro não disponível neste tenant.";
  if (status === 422) return "Identificador ou filtro inválido. Confira a entrada.";
  if (status === 401) return "Sessão expirada. Autentique-se novamente.";
  return "Não foi possível consultar o diagnóstico. Isso não indica ausência de intenções ou de conversas.";
}
export function jevParams(filters = {}) {
  return {
    limit: 50,
    ...(Object.hasOwn(JEV_STATUSES, filters.status) ? { status: filters.status } : {}),
    ...(Object.hasOwn(JEV_INTENTS, filters.intent) ? { intent: filters.intent } : {}),
  };
}
export function jevLeadId(value) {
  const id = String(value || "").trim();
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(id) ? id : null;
}
export function jevScopeIsCurrent(requestedTenant, currentTenant, signal) {
  return Boolean(requestedTenant && requestedTenant === currentTenant && !signal?.aborted);
}
export function jevTime(value) {
  if (!value || !Number.isFinite(Date.parse(value))) return "Horário desconhecido";
  return `${new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "medium", timeZone: "UTC" }).format(new Date(value))} UTC`;
}

export const FUNNEL_STATUS = { active: "Ativos", inactive: "Inativos", converted: "Convertidos comprovados", lost: "Perdidos confirmados", indeterminate: "Indeterminados" };
export const FILTER_KEYS = ["start", "end", "observed_at", "inactivity_hours", "return_window_days", "stage", "origin", "campaign_id", "ad_id", "status"];
export function defaultFunnelFilters(now = new Date()) {
  const end = new Date(now);
  const start = new Date(end.getTime() - 90 * 86400000);
  return { start: start.toISOString(), end: end.toISOString(), inactivity_hours: 168, return_window_days: 7, stage: "", origin: "", campaign_id: "", ad_id: "", status: "" };
}
export function allowlistedFunnelFilters(value) {
  if (!value || typeof value !== "object") return {};
  return Object.fromEntries(FILTER_KEYS.filter(key => typeof value[key] === "string" || (typeof value[key] === "number" && Number.isFinite(value[key]))).map(key => [key, value[key]]));
}
export function funnelParams(filters, extra = {}) {
  return Object.fromEntries(Object.entries({ ...allowlistedFunnelFilters(filters), ...extra }).filter(([, value]) => value !== "" && value !== null && value !== undefined));
}
export function funnelCount(value) { return value === null || value === undefined || !Number.isFinite(Number(value)) ? "Não registrado" : Number(value).toLocaleString("pt-BR"); }
export function funnelPercent(value) { return value === null || value === undefined || !Number.isFinite(Number(value)) ? "Desconhecida" : `${(Number(value) * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}%`; }
export function funnelRate(value) {
  if (!value || typeof value !== "object") return funnelPercent(value);
  const ratio = funnelPercent(value.value);
  return `${ratio} · ${funnelCount(value.numerator)} / ${funnelCount(value.denominator)}`;
}
export function funnelTime(value) { if (!value || Number.isNaN(new Date(value).getTime())) return "Não registrado"; return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short", timeZone: "UTC" }).format(new Date(value)); }
export function inputUtcTime(value) { if (!value || Number.isNaN(new Date(value).getTime())) return ""; return new Date(value).toISOString().slice(0, 16); }
export function utcInputValue(value) { return value ? `${value}:00.000Z` : ""; }
export function funnelError(error) {
  const status = error?.response?.status;
  if (status === 404) return "Dashboard indisponível ou filtro não encontrado. O Dashboard operacional e o Inbox continuam disponíveis.";
  if (status === 403) return "Sem autorização para consultar o funil deste tenant.";
  if (status === 422 || status === 400) return "Revise o período e os filtros. A janela aceita até 366 dias; o horizonte não pode estar no futuro.";
  return "Não foi possível consultar as evidências. Uma falha de consulta não significa ausência de contatos.";
}
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
export function journeyInboxPath(leadId) { return UUID.test(String(leadId || "")) ? `/painel/inbox?lead=${encodeURIComponent(leadId)}&journey=1` : null; }
export function journeyLeadFromSearch(search) { const params = new URLSearchParams(search); const id = params.get("lead"); return params.get("journey") === "1" && UUID.test(id || "") ? id : null; }
export function optionValue(option) { return typeof option === "string" ? option : String(option?.key ?? option?.value ?? option?.id ?? ""); }
export function optionLabel(option) { return typeof option === "string" ? option : String(option?.label ?? option?.name ?? option?.key ?? option?.value ?? option?.id ?? "Não identificado"); }
export function durationLabel(value) {
  if (typeof value === "number") return `${funnelCount(value)} h`;
  if (!value || typeof value !== "object") return "Não registrado";
  const median = value.median ?? value.median_hours ?? value.p50;
  const maximum = value.max ?? value.max_hours;
  return `Mediana ${median === null || median === undefined ? "desconhecida" : `${funnelCount(median)} h`}${maximum === null || maximum === undefined ? "" : ` · máximo ${funnelCount(maximum)} h`}`;
}
export function funnelRevenue(revenue) {
  if (!revenue || !Array.isArray(revenue.by_currency) || !revenue.by_currency.length) return "Receita desconhecida · sem pagamento comprovado";
  return revenue.by_currency.map(row => {
    const amount = row.net ?? row.amount ?? row.net_amount ?? row.revenue;
    if (amount === null || amount === undefined || !Number.isFinite(Number(amount))) return `${row.currency || "Moeda desconhecida"}: receita desconhecida`;
    return `${row.currency || "Moeda desconhecida"} ${Number(amount).toLocaleString("pt-BR", { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  }).join(" · ");
}

export function aggregateCsvIsSafe(csv) {
  if (typeof csv !== "string") return false;
  const header = csv.replace(/^\uFEFF/, "").split(/\r?\n/, 1)[0];
  return header === "section,metric,value,numerator,denominator,state,currency"
    && !/\b(?:display_name|lead_id|conversation_id|telefone|phone|email|nome_contato|customer_name)\b/i.test(csv)
    && !/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/i.test(csv)
    && !/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i.test(csv);
}

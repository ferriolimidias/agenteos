export const JOURNEY_CATEGORIES = {
  acquisition: "Aquisição", conversation: "Conversas", message: "Mensagens", crm: "CRM e tags",
  commercial: "Eventos comerciais", followup: "Follow-ups", campaign: "Campanhas WhatsApp",
  checkout: "Checkout", payment: "Pagamentos", booking: "Agendamentos",
};
const STATES = {
  observed: { label: "Observado", tone: "active" },
  unknown: { label: "Desconhecido · não observado nesta fonte", tone: "warning" },
  pending: { label: "Em andamento", tone: "info" },
  not_occurred: { label: "Não ocorreu · evidência explícita", tone: "default" },
};
export function journeyState(state) { return STATES[state] || STATES.unknown; }
export function journeyError(error) {
  const code = error?.response?.status;
  if (code === 404) return "Jornada indisponível ou desabilitada para este contato. A conversa continua disponível.";
  if (code === 403) return "Sem autorização para consultar a jornada deste tenant.";
  if (code === 400 || code === 422) return "Não foi possível validar o filtro ou cursor. Atualize a jornada.";
  return "Não foi possível consultar a jornada. A ausência de resposta não significa ausência de fatos.";
}
export function mergeJourney(previous, incoming) {
  const items = new Map(previous.map(item => [item.id, item]));
  for (const item of incoming) if (!items.has(item.id)) items.set(item.id, item);
  return [...items.values()];
}
export const JOURNEY_TIME_ZONE = "America/Sao_Paulo";
export function journeyTime(value, zone = JOURNEY_TIME_ZONE) {
  const date = new Date(value);
  if (!value || Number.isNaN(date.getTime())) return "Timestamp não disponível";
  // "local" was browser-dependent. Keep callers compatible while always using
  // the business timezone; explicit UTC is reserved for technical evidence.
  return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "medium", timeZone: zone === "UTC" ? "UTC" : JOURNEY_TIME_ZONE }).format(date);
}
// Defense in depth: only named scalar facts are rendered, never arbitrary payloads.
export const JOURNEY_DETAIL_LABELS = {
  channel: "Canal", direction: "Direção", delivery_status: "Estado de entrega", event_name: "Evento comercial",
  historical_no_send: "Histórico sem envio", attribution_status: "Atribuição", campaign_id: "Campanha comprovada",
  amount: "Valor", currency: "Moeda", verified: "Verificado pela fonte", scheduled_at: "Programado em",
  payment_status: "Estado informado do pagamento (verificação separada)",
  adset_id: "Adset comprovado", ad_id: "Anúncio comprovado", creative_id: "Criativo comprovado", post_id: "Post comprovado",
  tag_id: "ID da tag", tag_name: "Tag registrada", action: "Ação registrada", actor_source: "Origem da ação",
  external_dispatch_policy: "Política de envio externo", resolution_status: "Resolução da atribuição",
  intervention: "Intervenção WhatsApp", started_at: "Iniciado em", ended_at: "Fim planejado", attempt_count: "Tentativas",
  provider_ack_is_not_delivery: "Ack do provedor não comprova entrega", checkout_reference_present: "Referência de checkout presente",
  payment_reference_present: "Referência de pagamento presente", source_projection: "Projeção de origem",
  stage_id: "ID da etapa", stage_key: "Chave da etapa",
  source_tag_event_id: "Evidência de tag relacionada", source_attribution_touch_id: "Evidência de atribuição relacionada",
  followup_mode: "Modalidade de follow-up",
};
export function safeJourneyDetails(details) {
  return Object.entries(JOURNEY_DETAIL_LABELS).filter(([key]) => {
    const value = details?.[key];
    return value !== null && value !== undefined && ["string", "boolean", "number"].includes(typeof value);
  }).map(([key, label]) => {
    const value = details[key];
    if (["scheduled_at", "started_at", "ended_at"].includes(key)) {
      return { key, label, value: journeyTime(value), ...(typeof value === "string" && !Number.isNaN(new Date(value).getTime()) ? { original: value } : {}) };
    }
    return { key, label, value: typeof value === "boolean" ? (value ? "sim" : "não") : String(value).slice(0, 200) };
  });
}

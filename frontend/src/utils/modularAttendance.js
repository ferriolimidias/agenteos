export const MODULAR_MODULES = Object.freeze({
  qualification: "Qualificação", products_services: "Produtos e serviços", pricing_conditions: "Preços e condições",
  objections_questions: "Dúvidas e objeções", trust_guarantees: "Confiança e garantias", availability: "Disponibilidade",
  scheduling: "Agendamento", offer_closing: "Oferta e fechamento", human_handoff: "Transferência humana",
});
export const JEV_PREVIEW_EXAMPLES = Object.freeze(["schedule_multi", "multi_price_trust", "close_human", "price_timing", "support_payment", "uncertain", "fit_conditions"]);
const contexts = ["complete", "incomplete", "conflicting"], channels = ["whatsapp", "instagram", "simulator"];
export function previewScopeCurrent(scope, tenant, token, signal) {
  return Boolean(scope?.tenant && scope.tenant === tenant && scope.token === token && !signal?.aborted);
}
export function canPreviewProvider(status, exampleId, examples = []) {
  return status?.provider?.jev_configured === true && JEV_PREVIEW_EXAMPLES.includes(exampleId) && examples.some(item => item.id === exampleId && item.jev_eligible === true);
}
export function buildPreviewRequest({ source, exampleId, message, contextMode, channel, useJev = false }, status, examples) {
  if (!contexts.includes(contextMode) || !channels.includes(channel)) throw new Error("INVALID_PREVIEW");
  const body = { context_mode: contextMode, channel, use_jev: false };
  if (source === "example" && examples.some(item => item.id === exampleId)) {
    body.example_id = exampleId;
    if (useJev) {
      if (!canPreviewProvider(status, exampleId, examples)) throw new Error("INVALID_PREVIEW");
      body.use_jev = true;
    }
  } else if (source === "custom" && !useJev && typeof message === "string" && message.trim() && message.length <= 2000) {
    body.message = message.trim();
  } else throw new Error("INVALID_PREVIEW");
  return body;
}
export function modularError(error) {
  if (error?.message === "INVALID_PREVIEW") return "Confira o exemplo ou a mensagem e os filtros da simulação.";
  if (error?.message === "INVALID_RESPONSE") return "A simulação retornou um resultado incompleto. Tente novamente.";
  if (error?.response?.status === 403) return "Você não tem acesso à simulação desta empresa.";
  if (error?.response?.status === 404) return "O exemplo ou a empresa não está disponível.";
  if (error?.response?.status === 422) return "Confira a mensagem e as opções da simulação.";
  if (error?.code === "ECONNABORTED" || error?.code === "ETIMEDOUT") return "A simulação demorou além do limite. Sua mensagem foi preservada; tente novamente.";
  return "Não foi possível simular agora. Sua mensagem foi preservada; tente novamente.";
}
export function metricValue(value, suffix = "") {
  return typeof value === "number" && Number.isFinite(value) && value >= 0
    ? new Intl.NumberFormat("pt-BR", { maximumFractionDigits: 3 }).format(value) + suffix : "Não medido";
}
export function moduleVersion(value) { return typeof value === "string" && /^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$/.test(value) ? value : "Versão desconhecida"; }
export function previewModules(items) {
  return Array.isArray(items) ? items.filter(item => Object.hasOwn(MODULAR_MODULES, item?.id)).slice(0, 9).map(item => ({ id: item.id, title: MODULAR_MODULES[item.id], version: moduleVersion(item.version) })) : [];
}
export function previewResponse(data) {
  if (data?.schema_version !== "a07.v1" || typeof data.response_text !== "string" || !data.response_text.trim() || data.response_text.length > 10000 || typeof data.legacy_response_text !== "string" || data.legacy_response_text.length > 10000) throw new Error("INVALID_RESPONSE");
  return data;
}
export function previewStage(stage) {
  if (stage?.status === "unknown" || !stage?.value) return "Etapa desconhecida";
  const labels = { consideration: "Consideração", discovery: "Descoberta", decision: "Decisão", support: "Suporte" };
  const value = labels[stage.value] || String(stage.value).slice(0, 100);
  return `${value} · ${["synthetic", "synthetic_fixture"].includes(stage.source) ? "sintética" : ["canonical", "crm_snapshot"].includes(stage.source) ? "canônica" : "fonte não confirmada"}`;
}
export function previewClassification(jev) {
  return jev?.provider_used === true && jev?.status === "completed" && ["observed_external", "typesafe"].includes(jev?.source) ? "JEV: decisão externa observada em exemplo sintético" : "Seleção local determinística; JEV não utilizado";
}
export function previewTopics(items) {
  const labels = { ...MODULAR_MODULES, price: "Preço", conditions: "Condições", trust: "Confiança", availability: "Disponibilidade", booking: "Agendamento", human: "Atendimento humano", closing: "Fechamento", questions: "Dúvidas", objection: "Objeção", support: "Suporte" };
  return Array.isArray(items) ? [...new Set(items.filter(key => Object.hasOwn(labels, key)))].map(key => labels[key]) : [];
}

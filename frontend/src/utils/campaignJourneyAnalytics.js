const STATE_LABELS = { observed: "Observado", partial: "Parcial", unknown: "Desconhecido", not_applicable: "Não aplicável" };

export function formatA10Metric(metric) {
  if (!metric || metric.value === null || metric.value === undefined) return "—";
  const value = Number(metric.value);
  if (!Number.isFinite(value)) return "—";
  return `${(value * 100).toLocaleString("pt-BR", { maximumFractionDigits: 1 })}% · ${metric.numerator ?? 0} / ${metric.denominator ?? 0}`;
}

export function a10StateLabel(value) {
  return STATE_LABELS[value] || STATE_LABELS.unknown;
}

export function a10Rate(metric) {
  if (!metric || metric.value === null || metric.value === undefined) return a10StateLabel(metric?.state);
  return formatA10Metric(metric);
}

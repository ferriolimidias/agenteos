export const OUTCOMES = {
  accepted: { label: "Aceito pela Meta", tone: "active" },
  rejected: { label: "Rejeitado", tone: "danger" },
  unknown: { label: "Entrega desconhecida", tone: "warning" },
  not_sent: { label: "Não enviado", tone: "default" },
  prepared: { label: "Preparado", tone: "info" },
  blocked: { label: "Bloqueado", tone: "warning" },
};

export function outcomePresentation(value) {
  return OUTCOMES[value] || { label: "Estado não reconhecido", tone: "warning" };
}

export function operationsParams(filters, offset = 0, limit = 20) {
  const params = { limit, offset };
  for (const key of ["outcome", "transport", "event_name"]) {
    const value = String(filters[key] || "").trim();
    if (value) params[key] = value;
  }
  for (const key of ["start_at", "end_at"]) {
    if (!filters[key]) continue;
    const date = new Date(filters[key]);
    if (Number.isNaN(date.getTime())) throw new Error("Informe uma data válida.");
    params[key] = date.toISOString();
  }
  if (params.start_at && params.end_at && params.start_at > params.end_at) {
    throw new Error("O início deve ser anterior ao fim do período.");
  }
  return params;
}

export function emptyObservation(summary) {
  return summary?.observation_status === "NO_EVENT_OBSERVED";
}

export function operationsError(error) {
  const status = error?.response?.status;
  if (status === 404) return "A visão operacional está indisponível ou foi desabilitada. Isso não indica ausência de eventos.";
  if (status === 403) return "Sua conta não tem acesso às evidências deste tenant.";
  if (status === 401) return "Sua sessão precisa ser autenticada novamente.";
  if (status === 400 || status === 422) return "Os filtros não são válidos. Confira o período e tente novamente.";
  return "Não foi possível consultar as evidências. O estado de entrega não pode ser confirmado.";
}

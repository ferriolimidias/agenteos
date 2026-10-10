function stringifyErrorValue(value) {
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) {
    return value.map((item) => {
      if (typeof item === "string") return item;
      if (!item || typeof item !== "object") return String(item ?? "");
      const location = Array.isArray(item.loc) ? item.loc.filter(Boolean).join(".") : "";
      const message = item.msg || item.message || item.detail || item.error;
      const normalized = stringifyErrorValue(message);
      return location && normalized ? `${location}: ${normalized}` : normalized;
    }).filter(Boolean).join("; ");
  }
  if (value && typeof value === "object") return stringifyErrorValue(value.message || value.msg || value.detail || value.error);
  return "";
}

export function normalizeApiError(error, fallback = "Não foi possível concluir a operação.") {
  const data = error?.response?.data;
  const candidates = [data?.detail, data?.message, data?.error?.message, data?.error, error?.message];
  for (const candidate of candidates) {
    const normalized = stringifyErrorValue(candidate);
    if (normalized) return normalized;
  }
  return fallback;
}

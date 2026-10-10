const TAG_ARRAY_KEYS = ["tags", "items", "values", "data", "list"];

export function normalizeLeadTags(rawTags) {
  if (Array.isArray(rawTags)) return rawTags;
  if (!rawTags) return [];

  if (typeof rawTags === "string") {
    const normalized = rawTags.trim();
    return normalized ? [normalized] : [];
  }

  if (typeof rawTags === "object") {
    for (const key of TAG_ARRAY_KEYS) {
      if (Array.isArray(rawTags[key])) return rawTags[key];
    }

    return Object.values(rawTags).filter(
      (value) => value && (typeof value === "string" || typeof value === "object")
    );
  }

  return [];
}

export function normalizeTagValue(tag) {
  if (!tag) return "";

  if (typeof tag === "object") {
    return String(tag.id || tag.nome || tag.label || "").trim();
  }

  return String(tag).trim();
}

export function normalizeLeadTagsForApi(rawTags) {
  const output = [];
  const seen = new Set();

  normalizeLeadTags(rawTags).forEach((tag) => {
    const normalized = normalizeTagValue(tag);
    if (!normalized) return;
    const key = normalized.toLowerCase();
    if (seen.has(key)) return;
    seen.add(key);
    output.push(normalized);
  });

  return output;
}

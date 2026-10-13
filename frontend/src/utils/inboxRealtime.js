import { applyRealtimeReaction } from "./reactions.js";

const identity = (value) => value === undefined || value === null ? "" : String(value).trim();
const nonempty = (value) => typeof value === "string" && value.trim() !== "";
const record = (value) => value !== null && typeof value === "object" && !Array.isArray(value);

export function normalizePhone(value) {
  const raw = identity(value);
  return /^[+\d\s().-]+$/.test(raw) ? raw.replace(/\D/g, "") : raw;
}

export function conversationKey(tenant, lead) {
  const tenantId = identity(tenant);
  const leadId = identity(lead?.id);
  const phone = normalizePhone(lead?.telefone_contato ?? lead?.telefone);
  return tenantId && leadId && phone ? JSON.stringify([tenantId, leadId, phone]) : "";
}

export function leadMatches(lead, data, message = {}) {
  if (!lead) return false;
  const leadId = identity(data?.lead_id ?? data?.lead?.id);
  const conversationId = identity(data?.conversation_id || message?.conversation_id || message?.channel_conversation_id);
  const phone = normalizePhone(data?.telefone || data?.telefone_contato || message?.telefone);
  if (leadId && identity(lead.id)) return identity(lead.id) === leadId;
  if (conversationId && identity(lead.conversation_id)) return identity(lead.conversation_id) === conversationId;
  return Boolean(phone && normalizePhone(lead.telefone_contato || lead.telefone) === phone);
}

export function definedLeadPatch(payload = {}) {
  payload = record(payload) ? payload : {};
  const patch = {};
  for (const key of ["id", "conversation_id", "contact_id", "channel", "status_atendimento", "etapa_crm", "ultima_mensagem_data"]) {
    if (nonempty(payload[key]) || (key === "id" && typeof payload[key] === "number")) patch[key] = payload[key];
  }
  for (const [key, aliases] of Object.entries({
    nome_contato: ["nome_contato", "nome", "contact_display_name"],
    telefone_contato: ["telefone_contato", "telefone"],
  })) {
    const value = aliases.map((alias) => payload[alias]).find(nonempty);
    if (value !== undefined) patch[key] = value;
  }
  for (const key of ["ia_ativa", "bot_pausado"]) {
    if (typeof payload[key] === "boolean") patch[key] = payload[key];
  }
  for (const key of ["foto_url", "bot_pausado_ate"]) {
    if (payload[key] === null || typeof payload[key] === "string") patch[key] = payload[key];
  }
  for (const key of ["historico_resumo", "ultima_mensagem"]) {
    if (typeof payload[key] === "string") patch[key] = payload[key];
  }
  if (Array.isArray(payload.tags)) patch.tags = payload.tags;
  if (record(payload.dados_adicionais)) patch.dados_adicionais = payload.dados_adicionais;
  if (typeof payload.nao_lidas === "number" && Number.isFinite(payload.nao_lidas) && payload.nao_lidas >= 0) patch.nao_lidas = payload.nao_lidas;
  return patch;
}

const timestamp = (value) => new Date(value || 0).getTime() || 0;
const reaction = (message) => identity(message?.tipo_mensagem).toLowerCase() === "reaction";

export function patchLeadFromMessage(lead, data = {}, message = {}, { selected = false, isNew = false } = {}) {
  data = record(data) ? data : {};
  message = record(message) ? message : {};
  const patch = definedLeadPatch({
    ...data.lead,
    contact_display_name: data.contact_display_name ?? data.lead?.contact_display_name,
    telefone: data.telefone ?? data.lead?.telefone,
    conversation_id: data.conversation_id || message.conversation_id || message.channel_conversation_id || data.lead?.conversation_id,
  });
  const next = { ...lead, ...patch };
  if (selected) next.nao_lidas = 0;
  if (!isNew || reaction(message)) return next;
  const date = data.last_message_at || data.created_at || message.criado_em;
  if (date && timestamp(date) >= timestamp(lead?.ultima_mensagem_data)) {
    next.ultima_mensagem_data = date;
    const preview = data.last_message_preview ?? message.texto;
    if (typeof preview === "string") next.ultima_mensagem = preview;
  }
  const direction = identity(data.direction || message.direction).toLowerCase();
  const inbound = ["inbound", "outbound"].includes(direction) ? direction === "inbound" : typeof message.from_me === "boolean"
    ? !message.from_me : identity(data.tipo_evento).toLowerCase() === "nova_mensagem_inbound";
  if (!selected && inbound) {
    next.nao_lidas = Math.max(Number(lead?.nao_lidas || 0) + 1, Number(data.unread_count || 0));
  }
  return next;
}

function revisionFor(revisions, keys) {
  return Math.max(0, ...keys.filter(Boolean).map((key) => Number((revisions?.get?.(key) ?? revisions?.[key])?.revision ?? revisions?.get?.(key) ?? revisions?.[key] ?? 0) || 0));
}

function sameLead(a, b) {
  return leadMatches(a, { lead_id: b?.id, conversation_id: b?.conversation_id, telefone: b?.telefone_contato || b?.telefone });
}

const protectedLeadFields = new Set([
  "id", "conversation_id", "contact_id", "channel", "status_atendimento", "etapa_crm", "ultima_mensagem_data",
  "nome_contato", "nome", "contact_display_name", "telefone_contato", "telefone", "ia_ativa", "bot_pausado",
  "foto_url", "bot_pausado_ate", "historico_resumo", "ultima_mensagem", "tags", "dados_adicionais", "nao_lidas",
]);

function leadSnapshotPatch(lead) {
  // Full HTTP snapshots also carry fields used elsewhere by the existing Inbox.
  // Realtime partial payloads still use the stricter definedLeadPatch allowlist.
  const extra = Object.fromEntries(Object.entries(lead || {}).filter(([key, value]) => !protectedLeadFields.has(key) && value !== undefined));
  return { ...extra, ...definedLeadPatch(lead) };
}

export function mergeLeadSnapshot(current = [], remote = [], startRevision = 0, revisions = new Map()) {
  const seen = new Set();
  const merged = (Array.isArray(remote) ? remote : []).map((lead) => {
    const index = current.findIndex((item) => sameLead(item, lead));
    const patch = leadSnapshotPatch(lead);
    if (index < 0) return patch;
    seen.add(index);
    const previous = current[index];
    const version = revisions?.get?.(identity(previous.id));
    if (version?.fields) {
      const protectedFields = Object.fromEntries(Object.entries(version.fields)
        .filter(([, revision]) => revision > startRevision).map(([field]) => [field, previous[field]]));
      return { ...previous, ...patch, ...protectedFields };
    }
    const changed = revisionFor(revisions, [identity(previous.id)]) > startRevision;
    return changed ? { ...patch, ...previous } : { ...previous, ...patch };
  });
  current.forEach((lead, index) => {
    if (!seen.has(index) && revisionFor(revisions, [identity(lead.id)]) > startRevision) merged.push(lead);
  });
  return merged;
}

export function messageIdentity(message) {
  return identity(message?.id) || identity(message?.external_message_id);
}

function messageKeys(message) {
  return [identity(message?.id), identity(message?.external_message_id)].filter(Boolean);
}

function sameMessage(a, b) {
  const keys = messageKeys(a);
  return messageKeys(b).some((key) => keys.includes(key));
}

function definedMessagePatch(message = {}) {
  const patch = {};
  for (const [key, value] of Object.entries(message)) {
    if (value === undefined) continue;
    if (["id", "external_message_id", "tipo_mensagem", "criado_em", "status"].includes(key) && !identity(value)) continue;
    if (["texto", "media_url", "metadata_json", "from_me", "reactions"].includes(key) && value === null) continue;
    patch[key] = value;
  }
  return patch;
}

function patchMessage(previous, incoming) {
  const patch = definedMessagePatch(incoming);
  if (patch.texto === "" && nonempty(previous?.texto)) delete patch.texto;
  if (patch.media_url === "" && nonempty(previous?.media_url)) delete patch.media_url;
  const next = { ...previous, ...patch };
  if (record(previous?.metadata_json) && record(patch.metadata_json)) {
    next.metadata_json = { ...previous.metadata_json, ...patch.metadata_json };
  }
  return next;
}

function pendingMatch(message, incoming) {
  if (identity(message?.status).toLowerCase() !== "pending" || !message?.from_me || !incoming?.from_me) return false;
  const type = identity(message.tipo_mensagem || "text").toLowerCase();
  const incomingType = identity(incoming.tipo_mensagem || "text").toLowerCase();
  if (type !== incomingType) return false;
  return ["image", "audio", "video", "document"].includes(type) || identity(message.texto) === identity(incoming.texto);
}

function sortedMessages(messages) {
  return [...messages].sort((a, b) => timestamp(a?.criado_em) - timestamp(b?.criado_em) || messageIdentity(a).localeCompare(messageIdentity(b)));
}

export function upsertMessage(messages = [], message, { allowInsert = true } = {}) {
  if (!message || typeof message !== "object") return messages;
  if (reaction(message)) return applyRealtimeReaction(messages, message);
  let index = messages.findIndex((item) => sameMessage(item, message));
  if (index < 0 && allowInsert) index = messages.findIndex((item) => pendingMatch(item, message));
  if (index < 0) {
    if (!allowInsert || !messageIdentity(message)) return messages;
    return sortedMessages([...messages, definedMessagePatch(message)]);
  }
  const previous = messages[index];
  const next = patchMessage(previous, message);
  if (identity(previous.status).toLowerCase() === "pending" && !sameMessage(previous, message) && message.status === undefined) delete next.status;
  return sortedMessages(messages.map((item, itemIndex) => itemIndex === index ? next : item));
}

export function mergeMessageSnapshot(current = [], remote = [], startRevision = 0, revisions = new Map(), observedPatches = new Map()) {
  let merged = [...current];
  const reactions = [];
  for (const message of Array.isArray(remote) ? remote : []) {
    if (reaction(message)) { reactions.push(message); continue; }
    const existing = merged.find((item) => sameMessage(item, message));
    const changed = existing && revisionFor(revisions, messageKeys(existing)) > startRevision;
    const observed = messageKeys(message).map(key => observedPatches.get(key)).find(Boolean);
    let incoming = changed ? patchMessage(message, existing) : message;
    if (observed) {
      incoming = patchMessage(incoming, observed);
      // A later HTTP read/delivery acknowledgement can advance status, but an
      // unversioned snapshot cannot undo an observed realtime edit or reaction.
      const ranks = { pending: 0, sent: 1, delivered: 2, read: 3 };
      for (const candidate of [message.status, existing?.status]) {
        if (ranks[candidate] !== undefined && ranks[incoming.status] !== undefined && ranks[candidate] > ranks[incoming.status]) incoming.status = candidate;
      }
    }
    merged = upsertMessage(merged, incoming);
  }
  for (const message of reactions) {
    const target = merged.find((item) => identity(item.external_message_id) === identity(message?.metadata_json?.reaction?.message_id));
    const observed = target && messageKeys(target).map(key => observedPatches.get(key)).find(Boolean);
    if (!observed?.reactions && (!target || revisionFor(revisions, messageKeys(target)) <= startRevision)) merged = applyRealtimeReaction(merged, message);
  }
  return sortedMessages(merged);
}

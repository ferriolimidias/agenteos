import test from "node:test";
import assert from "node:assert/strict";
import {
  conversationKey, normalizePhone, normalizeLeadId, leadMatches, definedLeadPatch,
  patchLeadFromMessage, mergeLeadSnapshot, messageIdentity, upsertMessage,
  mergeMessageSnapshot,
} from "./inboxRealtime.js";

const lead = {
  id: "lead-a", conversation_id: "conversation-a", nome_contato: "Contato sintético",
  telefone_contato: "+55 (11) 99999-0001", status_atendimento: "manual",
  etapa_crm: "qualificado", bot_pausado: true, ia_ativa: false,
  tags: [{ id: "tag-a" }], nao_lidas: 2, ultima_mensagem: "Anterior",
  ultima_mensagem_data: "2026-10-13T12:00:00Z",
};
const message = {
  id: 1, external_message_id: "wamid.one", texto: "Primeira mensagem sintética",
  criado_em: "2026-10-13T12:00:00Z", from_me: false, tipo_mensagem: "text", status: "delivered",
};
const incoming = {
  id: 2, external_message_id: "wamid.two", texto: "Nova mensagem sintética",
  criado_em: "2026-10-13T12:01:00Z", from_me: false, tipo_mensagem: "text",
};
const reaction = {
  id: "reaction-one", external_message_id: "wamid.reaction", tipo_mensagem: "reaction",
  direction: "inbound", metadata_json: { reaction: { message_id: "wamid.one", emoji: "👍" } },
};

test("conversation keys isolate tenants and contacts and reject incomplete identities", () => {
  assert.equal(conversationKey("tenant-a", lead), JSON.stringify(["tenant-a", "lead-a"]));
  assert.notEqual(conversationKey("tenant-b", lead), conversationKey("tenant-a", lead));
  assert.notEqual(conversationKey("tenant-a", { ...lead, id: "lead-b" }), conversationKey("tenant-a", lead));
  assert.equal(conversationKey("tenant-a", { id: "lead-a" }), conversationKey("tenant-a", lead));
  assert.equal(conversationKey("tenant-a", { telefone_contato: lead.telefone_contato }), "");
  assert.equal(conversationKey("", lead), "");
});

test("phone formatting is canonical without destroying opaque channel identities", () => {
  assert.equal(normalizePhone(" +55 (11) 99999-0001 "), normalizePhone("5511999990001"));
  assert.equal(normalizePhone(" instagram:user_123 "), "instagram:user_123");
  assert.equal(normalizePhone("123@lid"), "123@lid");
  assert.equal(normalizePhone(null), "");
});

test("event matching supports lead, conversation and normalized phone without empty matches", () => {
  assert.equal(leadMatches(lead, { lead_id: "lead-a" }), true);
  assert.equal(leadMatches(lead, {}, { channel_conversation_id: "conversation-a" }), true);
  assert.equal(leadMatches(lead, { telefone: "5511999990001" }), true);
  assert.equal(leadMatches(lead, { lead_id: "lead-b", telefone: "5511999990002" }), false);
  assert.equal(leadMatches(lead, {}), false);
  assert.equal(leadMatches(null, { lead_id: "lead-a" }), false);
});

test("partial lead payload never erases identity, status, stage or tags with absent values", () => {
  const patch = definedLeadPatch({ nome: undefined, telefone: "", status_atendimento: null, etapa_crm: "", tags: undefined, ia_ativa: null });
  assert.deepEqual(patch, {});
  assert.deepEqual({ ...lead, ...patch }, lead);
  assert.deepEqual(definedLeadPatch({ nome: "Nome explícito", telefone: "551100000001" }), { nome_contato: "Nome explícito", telefone_contato: "551100000001" });
});

test("explicit false, empty tags, nullable photo and valid contextual fields remain valid", () => {
  const payload = { ia_ativa: false, bot_pausado: false, tags: [], foto_url: null, bot_pausado_ate: null, historico_resumo: "", dados_adicionais: {}, nao_lidas: 0 };
  assert.deepEqual(definedLeadPatch(payload), payload);
  assert.deepEqual(definedLeadPatch({ ia_ativa: "false", bot_pausado: 0, tags: {}, dados_adicionais: [], nao_lidas: -1, permission: "admin" }), {});
});

test("missing realtime identity retains the selected conversation and its commercial state", () => {
  const next = patchLeadFromMessage(lead, { lead_id: lead.id }, incoming, { selected: true, isNew: true });
  assert.equal(next.nome_contato, lead.nome_contato);
  assert.equal(next.telefone_contato, lead.telefone_contato);
  assert.equal(next.status_atendimento, "manual");
  assert.deepEqual(next.tags, lead.tags);
  assert.equal(next.etapa_crm, "qualificado");
  assert.equal(next.ia_ativa, false);
  assert.equal(next.nao_lidas, 0);
  assert.equal(next.ultima_mensagem, incoming.texto);
  assert.equal(next.ultima_mensagem_data, incoming.criado_em);
});

test("genuinely new inbound events count unread once; duplicates and updates never count", () => {
  const first = patchLeadFromMessage(lead, {}, incoming, { isNew: true });
  assert.equal(first.nao_lidas, 3);
  assert.deepEqual(patchLeadFromMessage(first, {}, incoming, { isNew: false }), first);
  assert.equal(patchLeadFromMessage(lead, { direction: "outbound" }, incoming, { isNew: true }).nao_lidas, 2);
  assert.equal(patchLeadFromMessage(lead, { tipo_evento: "nova_mensagem_inbound" }, { ...incoming, from_me: undefined }, { isNew: true }).nao_lidas, 3);
  assert.equal(patchLeadFromMessage(lead, { direction: "unknown" }, incoming, { isNew: true }).nao_lidas, 3);
  assert.equal(patchLeadFromMessage(lead, { unread_count: 8 }, incoming, { isNew: true }).nao_lidas, 8);
});

test("reactions and old messages do not replace a newer preview or timestamp", () => {
  assert.deepEqual(patchLeadFromMessage(lead, {}, reaction, { isNew: true }), lead);
  const older = patchLeadFromMessage(lead, {}, { ...incoming, criado_em: "2026-10-13T11:59:00Z" }, { isNew: true });
  assert.equal(older.ultima_mensagem, "Anterior");
  assert.equal(older.ultima_mensagem_data, lead.ultima_mensagem_data);
  assert.equal(older.nao_lidas, 3);
});

test("lead snapshots retain fields updated since request start and new realtime contacts", () => {
  const live = { ...lead, ultima_mensagem: incoming.texto, ultima_mensagem_data: incoming.criado_em, tags: [] };
  const newLead = { ...lead, id: "lead-b", conversation_id: "conversation-b", telefone_contato: "5511999990002" };
  const revisions = new Map([[lead.id, 11], [newLead.id, 12]]);
  const merged = mergeLeadSnapshot([live, newLead], [{ ...lead, nome_contato: "Nome antigo" }], 10, revisions);
  assert.equal(merged.length, 2);
  assert.deepEqual(merged[0], live);
  assert.deepEqual(merged[1], newLead);
});

test("fresh lead snapshots apply only defined fields and preserve existing identity", () => {
  const merged = mergeLeadSnapshot([lead], [{ id: lead.id, nome_contato: null, telefone_contato: "", status_atendimento: "fechado", tags: [] }], 10, new Map([[lead.id, 10]]));
  assert.equal(merged[0].nome_contato, lead.nome_contato);
  assert.equal(merged[0].telefone_contato, lead.telefone_contato);
  assert.equal(merged[0].status_atendimento, "fechado");
  assert.deepEqual(merged[0].tags, []);
});

test("full HTTP lead snapshots preserve extra API fields without permitting partial-event extras", () => {
  const remote = { ...lead, criado_em: "2026-10-01T12:00:00Z", valor_conversao: 42, origem: "synthetic", campo_futuro: { seguro: true } };
  assert.deepEqual(mergeLeadSnapshot([], [remote]), [remote]);
  assert.equal(definedLeadPatch(remote).valor_conversao, undefined);
  assert.equal(mergeLeadSnapshot([lead], [remote])[0].criado_em, remote.criado_em);
});

test("empty snapshots are valid but retain only leads changed after request start", () => {
  assert.deepEqual(mergeLeadSnapshot([], []), []);
  assert.deepEqual(mergeLeadSnapshot([lead], [], 5, new Map([[lead.id, 5]])), []);
  assert.deepEqual(mergeLeadSnapshot([lead], [], 5, new Map([[lead.id, 6]])), [lead]);
});

test("message identity canonicalizes numeric ids and supports external-only messages", () => {
  assert.equal(messageIdentity(message), "1");
  assert.equal(messageIdentity({ external_message_id: "wamid.one" }), "wamid.one");
  assert.equal(messageIdentity({ id: 0 }), "0");
  assert.equal(messageIdentity({}), "");
});

test("duplicate numeric/string ids update one message without losing its content", () => {
  const merged = upsertMessage([message], { id: "1", status: "read", texto: undefined, media_url: undefined });
  assert.equal(merged.length, 1);
  assert.equal(merged[0].texto, message.texto);
  assert.equal(merged[0].status, "read");
  assert.deepEqual(upsertMessage(merged, { id: "1", status: "read" }), merged);
});

test("external id deduplication works before and after a persisted internal id arrives", () => {
  const original = { ...message, id: undefined };
  const merged = upsertMessage([original], { ...message, texto: "Atualizada" });
  assert.equal(merged.length, 1);
  assert.equal(merged[0].id, 1);
  assert.equal(merged[0].texto, "Atualizada");
  assert.equal(upsertMessage(merged, { external_message_id: "wamid.one", status: "read" }).length, 1);
});

test("partial message updates retain media, text, date and metadata when absent", () => {
  const media = { ...message, media_url: "/synthetic/image.png", metadata_json: { mime_type: "image/png", caption: "Legenda" } };
  const merged = upsertMessage([media], { id: 1, texto: null, media_url: null, criado_em: null, metadata_json: { size: 42 } });
  assert.equal(merged[0].media_url, media.media_url);
  assert.equal(merged[0].texto, media.texto);
  assert.equal(merged[0].criado_em, media.criado_em);
  assert.deepEqual(merged[0].metadata_json, { mime_type: "image/png", caption: "Legenda", size: 42 });
  const empty = upsertMessage([media], { id: 1, texto: "", media_url: "" });
  assert.equal(empty[0].texto, media.texto);
  assert.equal(empty[0].media_url, media.media_url);
  assert.equal(upsertMessage([], { ...media, texto: "", media_url: "" })[0].texto, "");
});

test("status updates cannot insert unknown messages when allowInsert is false", () => {
  assert.deepEqual(upsertMessage([message], { id: 2, status: "read" }, { allowInsert: false }), [message]);
  assert.equal(upsertMessage([message], { id: 1, status: "read" }, { allowInsert: false })[0].status, "read");
});

test("outbound acknowledgement replaces pending text without duplicating the bubble", () => {
  const pending = { id: "temp-one", status: "pending", from_me: true, texto: "Texto sintético", tipo_mensagem: "text", criado_em: incoming.criado_em };
  const acknowledgement = { id: 9, external_message_id: "wamid.sent", from_me: true, texto: "Texto sintético", tipo_mensagem: "text", criado_em: incoming.criado_em };
  const merged = upsertMessage([message, pending], acknowledgement);
  assert.equal(merged.length, 2);
  assert.equal(merged[1].id, 9);
  assert.notEqual(merged[1].status, "pending");
  assert.equal(upsertMessage(merged, acknowledgement).length, 2);
});

test("outbound media acknowledgement conciliates the matching type and retains local media", () => {
  const pending = { id: "temp-media", status: "pending", from_me: true, texto: "Enviando…", tipo_mensagem: "audio", media_url: "blob:synthetic", criado_em: incoming.criado_em };
  const merged = upsertMessage([pending], { id: 9, from_me: true, texto: "Áudio enviado", tipo_mensagem: "audio", criado_em: incoming.criado_em });
  assert.equal(merged.length, 1);
  assert.equal(merged[0].id, 9);
  assert.equal(merged[0].media_url, "blob:synthetic");
  assert.equal(upsertMessage([pending], { ...incoming, from_me: true }).length, 2);
});

test("pending text is not reconciled with another outbound text or an inbound echo", () => {
  const pending = { ...message, id: "temp-other", texto: "Outro texto", from_me: true, status: "pending" };
  assert.equal(upsertMessage([pending], { ...incoming, from_me: true }).length, 2);
  assert.equal(upsertMessage([pending], { ...incoming, texto: "Outro texto", from_me: false }).length, 2);
});

test("reaction events update the target once without a reaction bubble", () => {
  const first = upsertMessage([message], reaction);
  assert.equal(first.length, 1);
  assert.equal(first[0].reactions[0].emoji, "👍");
  assert.deepEqual(upsertMessage(first, reaction), first);
  assert.deepEqual(upsertMessage([incoming], reaction), [incoming]);
});

test("slow history snapshots cannot remove messages received while the GET was pending", () => {
  const current = upsertMessage([message], incoming);
  const merged = mergeMessageSnapshot(current, [message], 2, new Map([["2", 3]]));
  assert.deepEqual(merged, current);
  assert.deepEqual(mergeMessageSnapshot(current, [], 2, new Map([["2", 3]])), current);
});

test("late HTTP cannot overwrite newer status, content or reactions", () => {
  const live = { ...message, status: "read", texto: "Versão recebida via WS", reactions: [{ emoji: "👍", message_id: "wamid.reaction", direction: "inbound" }] };
  const merged = mergeMessageSnapshot([live], [{ ...message, reactions: [] }], 4, new Map([["1", 5]]));
  assert.deepEqual(merged, [live]);
  const byExternal = mergeMessageSnapshot([live], [{ ...message, id: "1", reactions: [] }], 4, new Map([["wamid.one", 5]]));
  assert.deepEqual(byExternal, [live]);
});

test("fresh snapshot fills historical fields without losing pending or realtime messages", () => {
  const pending = { id: "temp-two", status: "pending", from_me: true, texto: "Rascunho enviado sintético", criado_em: "2026-10-13T12:02:00Z" };
  const merged = mergeMessageSnapshot([message, incoming, pending], [{ id: "1", status: "read" }], 5, new Map([["1", 5]]));
  assert.equal(merged.length, 3);
  assert.equal(merged[0].texto, message.texto);
  assert.equal(merged[0].status, "read");
  assert.deepEqual(merged[2], pending);
});

test("historical reaction rows attach after their target and stale rows cannot erase live reactions", () => {
  const merged = mergeMessageSnapshot([], [reaction, message]);
  assert.equal(merged.length, 1);
  assert.equal(merged[0].reactions[0].emoji, "👍");
  const live = { ...message, reactions: [{ emoji: "❤️", message_id: "newer", direction: "inbound" }] };
  const late = mergeMessageSnapshot([live], [reaction, message], 10, new Map([["1", 11]]));
  assert.deepEqual(late, [live]);
});

test("a burst of fifty messages with duplicate deliveries remains complete and chronological", () => {
  const burst = Array.from({ length: 50 }, (_, index) => ({ ...incoming, id: index + 10, external_message_id: `wamid.burst.${index}`, criado_em: new Date(Date.parse(incoming.criado_em) + index * 1000).toISOString() }));
  let merged = [message];
  for (const item of [...burst].reverse()) merged = upsertMessage(merged, item);
  for (const item of burst) merged = upsertMessage(merged, { ...item, id: String(item.id) });
  assert.equal(merged.length, 51);
  assert.equal(new Set(merged.map(messageIdentity)).size, 51);
  assert.deepEqual(merged.map(messageIdentity), ["1", ...burst.map(messageIdentity)]);
  assert.equal(mergeMessageSnapshot(merged, [message], 0, new Map(burst.map((item) => [messageIdentity(item), 1]))).length, 51);
});

test("merge operations never mutate current arrays, remote arrays or frozen records", () => {
  const frozenLead = Object.freeze({ ...lead });
  const frozenMessage = Object.freeze({ ...message });
  const leads = Object.freeze([frozenLead]);
  const messages = Object.freeze([frozenMessage]);
  assert.doesNotThrow(() => patchLeadFromMessage(frozenLead, {}, incoming, { isNew: true }));
  assert.doesNotThrow(() => mergeLeadSnapshot(leads, Object.freeze([{ id: lead.id, tags: [] }])));
  assert.doesNotThrow(() => upsertMessage(messages, incoming));
  assert.doesNotThrow(() => mergeMessageSnapshot(messages, Object.freeze([incoming])));
  assert.equal(frozenLead.ultima_mensagem, "Anterior");
  assert.equal(frozenMessage.status, "delivered");
});


test("explicit contact and conversation IDs outrank a shared phone", () => {
  const a = { id: "a", conversation_id: "ca", telefone_contato: "+55 11 99999-9999", nome_contato: "A", tags: ["A"] };
  assert.equal(leadMatches(a, { lead_id: "b", conversation_id: "cb", telefone: "5511999999999" }), false);
  assert.equal(leadMatches(a, { conversation_id: "cb", telefone: "5511999999999" }), false);
  assert.equal(leadMatches(a, { telefone: "5511999999999" }), true);
  const merged = mergeLeadSnapshot([a], [{ id: "b", conversation_id: "cb", telefone_contato: a.telefone_contato, nome_contato: "B" }]);
  assert.equal(merged[0].id, "b");
  assert.equal(merged[0].nome_contato, "B");
  assert.equal(merged[0].tags, undefined);
});

test("lead reconciliation protects only fields changed during the request", () => {
  const current = [{ id: "a", nome_contato: "A", ultima_mensagem: "WS novo", tags: ["old"], ia_ativa: true }];
  const versions = new Map([["a", { revision: 12, fields: { ultima_mensagem: 12 } }]]);
  const result = mergeLeadSnapshot(current, [{ id: "a", nome_contato: "A", ultima_mensagem: "HTTP antigo", tags: ["new"], ia_ativa: false }], 10, versions);
  assert.equal(result[0].ultima_mensagem, "WS novo");
  assert.deepEqual(result[0].tags, ["new"]);
  assert.equal(result[0].ia_ativa, false);
});

test("revalidation started after a realtime edit still cannot undo unversioned observations", () => {
  const current = [{ id: "m", external_message_id: "ext", texto: "editado via WS", status: "read", reactions: [{ emoji: "🔥" }] }];
  const observed = new Map([["m", { texto: current[0].texto, status: "read", reactions: current[0].reactions }]]);
  const stale = [{ id: "m", external_message_id: "ext", texto: "texto anterior", status: "sent", reactions: [] }];
  const merged = mergeMessageSnapshot(current, stale, 20, new Map([["m", 10]]), observed);
  assert.equal(merged[0].texto, current[0].texto);
  assert.equal(merged[0].status, "read");
  assert.deepEqual(merged[0].reactions, current[0].reactions);
});

test("HTTP can fill unknown messages and advance proven delivery without undoing realtime edits", () => {
  const current = [{ id: "m", texto: "editado", status: "delivered" }];
  const observed = new Map([["m", { texto: "editado", status: "delivered" }]]);
  const remote = [{ id: "m", texto: "antigo", status: "read" }, { id: "gap", texto: "recuperado" }];
  const merged = mergeMessageSnapshot(current, remote, 20, new Map(), observed);
  assert.equal(merged.find(m => m.id === "m").status, "read");
  assert.equal(merged.find(m => m.id === "m").texto, "editado");
  assert.equal(merged.find(m => m.id === "gap").texto, "recuperado");
});


test("unversioned HTTP acknowledgements do not override observed failed or unknown states", () => {
  for (const status of ["failed", "error", "UNKNOWN"]) {
    const current = [{ id: "m", texto: "conteúdo", status }];
    const observed = new Map([["m", { status }]]);
    const result = mergeMessageSnapshot(current, [{ id: "m", status: "read" }], 20, new Map(), observed);
    assert.equal(result[0].status, status);
  }
});


test("HTTP list started after a realtime rename cannot revert observed identity or newer preview", () => {
  const current = [{ id: "a", nome_contato: "Nome canônico WS", telefone_contato: "NEWPHONE", ultima_mensagem: "novo", ultima_mensagem_data: "2026-10-13T12:00:00Z", tags: ["old"] }];
  const versions = new Map([["a", { revision: 10, fields: { nome_contato: 10, telefone_contato: 10, ultima_mensagem: 10, ultima_mensagem_data: 10 } }]]);
  const result = mergeLeadSnapshot(current, [{ id: "a", nome_contato: "Nome antigo", telefone_contato: "OLDPHONE", ultima_mensagem: "antigo", ultima_mensagem_data: "2026-10-13T11:00:00Z", tags: ["new"] }], 20, versions);
  assert.equal(result[0].nome_contato, current[0].nome_contato);
  assert.equal(result[0].telefone_contato, current[0].telefone_contato);
  assert.equal(result[0].ultima_mensagem, "novo");
  assert.deepEqual(result[0].tags, ["new"]);
});

test("a provably later HTTP last-message timestamp can advance the preview", () => {
  const current = [{ id: "a", ultima_mensagem: "anterior", ultima_mensagem_data: "2026-10-13T12:00:00Z" }];
  const versions = new Map([["a", { revision: 10, fields: { ultima_mensagem: 10, ultima_mensagem_data: 10 } }]]);
  const result = mergeLeadSnapshot(current, [{ id: "a", ultima_mensagem: "recuperado", ultima_mensagem_data: "2026-10-13T12:01:00Z" }], 20, versions);
  assert.equal(result[0].ultima_mensagem, "recuperado");
  assert.equal(result[0].ultima_mensagem_data, "2026-10-13T12:01:00Z");
});


test("contact cache survives mutable WhatsApp delivery address and added conversation metadata", () => {
  const original = { ...lead, telefone_contato: "551198765432" };
  const key = conversationKey("tenant-a", original);
  const updated = { ...original, telefone_contato: "5511998765432", conversation_id: "added" };
  assert.equal(conversationKey("tenant-a", updated), key);
  assert.equal(conversationKey("tenant-a", { ...updated, telefone_contato: null }), key);
});

test("UUID case aliases represent the same contact without collapsing opaque IDs or tenants", () => {
  const id = "a1000000-0000-0000-0000-000000000001";
  assert.equal(normalizeLeadId(id.toUpperCase()), id);
  assert.equal(conversationKey("tenant-a", { id }), conversationKey("tenant-a", { id: id.toUpperCase() }));
  assert.equal(leadMatches({ id }, { lead_id: id.toUpperCase() }), true);
  assert.notEqual(conversationKey("tenant-a", { id: "VendorA" }), conversationKey("tenant-a", { id: "vendora" }));
  assert.notEqual(conversationKey("TenantA", { id }), conversationKey("tenanta", { id }));
});

test("outbound acknowledgement followed by address patch retains every existing history row in the same cache", () => {
  const contact = { ...lead, telefone_contato: "551198765432" };
  const key = conversationKey("tenant-a", contact);
  const old = Array.from({ length: 36 }, (_, i) => ({ ...message, id: `old-${i}`, criado_em: "2026-10-13T10:00:00Z" }));
  const optimistic = { id: "temp-send", from_me: true, status: "pending", tipo_mensagem: "text", texto: "Envio sintético", criado_em: "2026-10-13T12:00:00Z" };
  const confirmed = { ...optimistic, id: "confirmed", status: "sent" };
  const cache = new Map([[key, upsertMessage([...old, optimistic], confirmed)]]);
  const event = { lead_id: contact.id, telefone: "5511998765432", direction: "outbound" };
  const after = patchLeadFromMessage(contact, event, confirmed, { selected: true, isNew: true });
  assert.equal(conversationKey("tenant-a", after), key);
  assert.equal(cache.get(conversationKey("tenant-a", after)).length, 37);
  assert.equal(cache.get(key).filter(m => m.id === "temp-send").length, 0);
  assert.equal(cache.get(key).filter(m => m.id === "confirmed").length, 1);
  assert.ok(old.every(m => cache.get(key).some(row => row.id === m.id)));
});

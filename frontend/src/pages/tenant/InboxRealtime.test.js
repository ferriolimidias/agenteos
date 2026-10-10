import { readFileSync } from "node:fs";
import test from "node:test";
import assert from "node:assert/strict";

const source = readFileSync(new URL("./Inbox.jsx", import.meta.url), "utf8");
const layoutSource = readFileSync(new URL("../../layouts/TenantLayout.jsx", import.meta.url), "utf8");
const stylesSource = readFileSync(new URL("../../index.css", import.meta.url), "utf8");

function sortChronologically(messages) {
  return [...messages].sort((a, b) => {
    const timeA = new Date(a?.criado_em || 0).getTime() || 0;
    const timeB = new Date(b?.criado_em || 0).getTime() || 0;
    if (timeA !== timeB) return timeA - timeB;
    return String(a?.id || "").localeCompare(String(b?.id || ""));
  });
}

function appendRealtime(messages, incoming) {
  if (incoming?.id && messages.some((message) => message?.id === incoming.id)) return messages;
  return sortChronologically([...messages, incoming]);
}

test("inbox accepts canonical realtime message and ai-state events", () => {
  assert.match(source, /tipoEventoV2 === "inbox\.message\.created"/);
  assert.match(source, /tipoEventoV2 === "inbox\.ai_state\.updated"/);
  assert.match(source, /const novaMensagem = data\?\.mensagem \|\| data\?\.message/);
});

test("realtime reactions update the target message instead of becoming a bubble", () => {
  assert.match(source, /applyRealtimeReaction/);
  assert.match(source, /tipo_mensagem \\|\\| \"\"\)\.toLowerCase\(\) === \"reaction\"/);
  assert.match(source, /if \(isReaction\) return prev/);
});

test("realtime handler keeps payload in scope for every websocket frame", () => {
  const parseIndex = source.indexOf('const data = JSON.parse(event.data || "{}");');
  const payloadIndex = source.indexOf("const payload = data?.payload || {};", parseIndex);
  assert.ok(parseIndex >= 0, "websocket frame parser must remain present");
  assert.ok(payloadIndex > parseIndex, "payload must be declared after frame parsing");

  const aiStateBlock = source.split('if (tipoEventoV2 === "inbox.ai_state.updated"')[1]
    .split('if (tipoEventoV2 === "status_ia_changed"')[0];
  assert.doesNotMatch(aiStateBlock, /const payload = data\?\.payload/);
  assert.match(aiStateBlock, /let manualEnabled;/);
  assert.match(aiStateBlock, /manualEnabled = data\?\.ai_state\?\.manual_enabled \?\? payload\?\.ia_ativa;/);

  // Contract guard for inbound/outbound, payload fallback, and state updates.
  assert.match(source, /nova_mensagem_inbound/);
  assert.match(source, /nova_mensagem_outbound/);
  assert.match(source, /const novaMensagem = data\?\.mensagem \|\| data\?\.message/);
  assert.match(source, /setMessages\(\(prev\) =>/);
  assert.match(source, /status_ia_changed/);
});

test("inbox refreshes conversation list and ai state from websocket without page reload", () => {
  assert.match(source, /void fetchLeads\(\);/);
  assert.match(source, /setAiEffectiveState\(translateAiState\(data\.ai_state\)\)/);
  assert.match(source, /manual_enabled/);
  assert.doesNotMatch(source, /window\.location\.reload/);
});

test("inbox photo fetch is cached and deduplicated per contact", () => {
  assert.match(source, /photoCacheRef = useRef\(new Map\(\)\)/);
  assert.match(source, /photoRequestsRef = useRef\(new Map\(\)\)/);
  assert.match(source, /photoAbortControllersRef = useRef\(new Map\(\)\)/);
  assert.match(source, /photoRequestsRef\.current\.has\(cacheKey\)/);
  assert.match(source, /photoCacheRef\.current\.set\(cacheKey, fotoUrl\)/);
});

test("inbox toggle sends explicit desired ai state", () => {
  assert.match(source, /api\.patch\(`/);
  assert.match(source, /enabled: desiredEnabled/);
  assert.doesNotMatch(source, /const wasPaused/);
  assert.match(source, /IA desligada nesta conversa/);
});

test("inbox websocket reconnect path remains enabled", () => {
  assert.match(source, /new WebSocket\(wsUrl\)/);
  assert.match(source, /Tentando reconectar/);
});

test("history and realtime keep oldest at top and latest at bottom", () => {
  const m1 = { id: "1", criado_em: "2026-08-14T10:00:00Z", texto: "M1" };
  const m2 = { id: "2", criado_em: "2026-08-14T10:01:00Z", texto: "M2" };
  const m3 = { id: "3", criado_em: "2026-08-14T10:02:00Z", texto: "M3" };
  assert.deepEqual(sortChronologically([m3, m1, m2]).map((item) => item.texto), ["M1", "M2", "M3"]);
  assert.deepEqual(appendRealtime([m1, m2], m3).map((item) => item.texto), ["M1", "M2", "M3"]);
  assert.deepEqual(sortChronologically([m1, m2, m3]).map((item) => item.texto), ["M1", "M2", "M3"]);
});

test("mixed client and AI messages remain chronological and duplicate realtime IDs are ignored", () => {
  const m1 = { id: "1", criado_em: "2026-08-14T10:00:00Z", from_me: false };
  const m2 = { id: "2", criado_em: "2026-08-14T10:01:00Z", from_me: true, origem_mensagem: "ia" };
  const m3 = { id: "3", criado_em: "2026-08-14T10:02:00Z", from_me: false };
  const ordered = appendRealtime(appendRealtime([m1], m2), m3);
  assert.deepEqual(ordered.map((item) => item.id), ["1", "2", "3"]);
  assert.strictEqual(appendRealtime(ordered, m3), ordered);
});

test("Inbox source appends realtime at the end, sorts ascending, and auto-scrolls to the bottom", () => {
  assert.match(source, /return \[\.\.\.lista, novaMensagem\]\.sort/);
  assert.match(source, /return timeA - timeB/);
  assert.doesNotMatch(source, /flex-col-reverse/);
  assert.doesNotMatch(source, /\.reverse\(/);
  assert.match(source, /messagesEndRef\.current\?\.scrollIntoView/);
});

test("Inbox keeps the chat as the viewport-level layout", () => {
  assert.doesNotMatch(source, /<PageHeader/);
  assert.match(source, /flex min-h-0 min-w-0 flex-1 flex-col overflow-hidden/);
  assert.match(source, /min-h-0 flex-1 overflow-y-auto overscroll-contain/);
  assert.match(source, /shrink-0 border-t border-gray-200/);
  assert.match(source, /lg:w-\[clamp\(280px,30vw,380px\)\]/);
});

test("Inbox removes only the redundant tenant header and preserves the admin banner", () => {
  assert.match(layoutSource, /isInboxRoute \? "hidden" : ""/);
  assert.match(layoutSource, /Acessando como:/);
  assert.match(layoutSource, /Sair e Voltar/);
  assert.match(stylesSource, /\.chat-shell-content[\s\S]*min-height: 0/);
  assert.match(stylesSource, /\.chat-shell-content[\s\S]*overflow: hidden/);
});

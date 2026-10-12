import test from "node:test";
import assert from "node:assert/strict";
import { JEV_INTENTS, jevConfidence, jevError, jevLeadId, jevMetric, jevNumber, jevParams, jevScopeIsCurrent, jevSignal, jevTime } from "./jevDiagnostics.js";

test("unknown monetary/latency values never become zero", () => {
  for (const value of [null, undefined, "", "0", NaN, Infinity, -1]) assert.equal(jevNumber(value), "Desconhecido");
  assert.equal(jevNumber(0, " USD"), "0 USD");
});
test("demo fixture measurements cannot be presented as provider metrics", () => {
  assert.equal(jevMetric({ status: "demo", metrics: { cost_usd: 123 } }, "cost_usd"), "Não medido no provider");
  assert.equal(jevMetric({ status: "completed", source: "synthetic", metrics: { cost_usd: 0.000042 } }, "cost_usd", " USD"), "0,000042 USD");
  assert.equal(jevMetric({ status: "provider_error" }, "cost_usd"), "Desconhecido");
});
test("confidence is bounded and does not imply certainty for missing values", () => {
  for (const value of [null, undefined, "0.8", -0.1, 1.1, NaN]) assert.equal(jevConfidence(value), "Confiança desconhecida");
  assert.equal(jevConfidence(0.85), "85%");
});
test("typed boolean signals preserve false instead of treating it as missing", () => {
  assert.equal(jevSignal(true), "Sim"); assert.equal(jevSignal(false), "Não");
  assert.equal(jevSignal(null), "Desconhecido"); assert.equal(jevSignal({ value: true }), "Desconhecido");
});
test("filters are allowlisted and never carry arbitrary tenant/secret/history fields", () => {
  assert.deepEqual(jevParams({ status: "provider_error", intent: "price", api_key: "hidden", empresa_id: "other", limit: 10000 }), { limit: 50, status: "provider_error", intent: "price" });
  assert.deepEqual(jevParams({ status: "live", intent: "arbitrary" }), { limit: 50 });
  assert.ok(Object.hasOwn(JEV_INTENTS, "human"));
});
test("only well-formed lead UUIDs can initiate a conversation lookup", () => {
  assert.equal(jevLeadId("../../admin"), null);
  assert.equal(jevLeadId("00000000-0000-0000-0000-000000000002?tenant=other"), null);
  assert.equal(jevLeadId(" 00000000-0000-0000-0000-000000000002 "), "00000000-0000-0000-0000-000000000002");
});
test("scope prevents delayed cross-tenant or aborted analysis from rendering", () => {
  const controller = new AbortController();
  assert.equal(jevScopeIsCurrent("tenant-a", "tenant-a", controller.signal), true);
  assert.equal(jevScopeIsCurrent("tenant-a", "tenant-b", controller.signal), false);
  controller.abort(); assert.equal(jevScopeIsCurrent("tenant-a", "tenant-a", controller.signal), false);
  assert.equal(jevScopeIsCurrent(null, null), false);
});
test("errors never echo provider response, credentials, or internal exception text", () => {
  const message = jevError({ message: "private-secret", response: { status: 503, data: { detail: "private-secret" } } });
  assert.doesNotMatch(message, /private-secret/);
  assert.match(message, /não indica ausência/);
  assert.match(jevError({ response: { status: 403 } }), /não autorizado/);
});
test("timestamps explicitly show UTC and unknown states", () => {
  assert.match(jevTime("2026-10-12T12:00:00Z"), /UTC$/);
  assert.equal(jevTime(null), "Horário desconhecido");
  assert.equal(jevTime("wrong"), "Horário desconhecido");
});

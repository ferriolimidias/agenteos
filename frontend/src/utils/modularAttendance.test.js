import { test } from "node:test";
import assert from "node:assert/strict";
import { buildPreviewRequest, canPreviewProvider, metricValue, modularError, moduleVersion, previewClassification, previewModules, previewResponse, previewScopeCurrent, previewStage } from "./modularAttendance.js";
const catalog = [{ id: "schedule_multi", jev_eligible: true }, { id: "multi_price_trust_availability", jev_eligible: false }];
const status = { provider: { jev_configured: true } };
const input = { source: "example", exampleId: "schedule_multi", contextMode: "complete", channel: "whatsapp" };
test("preview source is exclusive and arbitrary text never leaves local simulation", () => {
  assert.deepEqual(buildPreviewRequest(input, status, catalog), { example_id: "schedule_multi", context_mode: "complete", channel: "whatsapp", use_jev: false });
  assert.deepEqual(buildPreviewRequest({ ...input, source: "custom", message: " Quanto custa? " }, status, catalog), { message: "Quanto custa?", context_mode: "complete", channel: "whatsapp", use_jev: false });
  assert.throws(() => buildPreviewRequest({ ...input, source: "custom", message: "PRIVATE", useJev: true }, status, catalog));
  for (const invalid of [{ message: "", source: "custom" }, { message: "a".repeat(2001), source: "custom" }, { exampleId: "other" }, { channel: "live" }, { contextMode: "production" }]) assert.throws(() => buildPreviewRequest({ ...input, ...invalid }, status, catalog));
});
test("JEV requires configured provider and exact server-owned eligible example", () => {
  assert.equal(canPreviewProvider(status, "schedule_multi", catalog), true);
  for (const [provider, id, items] of [[{}, "schedule_multi", catalog], [status, "multi_price_trust_availability", catalog], [status, "schedule_multi", []], [status, "schedule_multi", [{ id: "schedule_multi" }]]]) assert.equal(canPreviewProvider(provider, id, items), false);
  assert.equal(buildPreviewRequest({ ...input, useJev: true }, status, catalog).use_jev, true);
  assert.throws(() => buildPreviewRequest({ ...input, exampleId: "multi_price_trust_availability", useJev: true }, status, catalog));
});
test("late tenant, session and cancelled responses are rejected", () => {
  const scope = { tenant: "A", token: "session-a" };
  assert.equal(previewScopeCurrent(scope, "A", "session-a"), true);
  for (const [tenant, token, signal] of [["B", "session-a"], ["A", "session-b"], ["A", "session-a", { aborted: true }]]) assert.equal(previewScopeCurrent(scope, tenant, token, signal), false);
  assert.equal(previewScopeCurrent({}, null, null), false);
});
test("unknown measured values remain unknown, actual zero remains zero", () => {
  for (const value of [null, undefined, "0", -1, NaN, Infinity]) assert.equal(metricValue(value), "Não medido");
  assert.equal(metricValue(0), "0"); assert.equal(metricValue(12, " ms"), "12 ms");
});
test("errors never display provider/HTTP detail or input text", () => {
  for (const statusCode of [403, 404, 422, 500, 503]) assert.doesNotMatch(modularError({ response: { status: statusCode, data: { detail: "PRIVATE_SECRET" } }, message: "PRIVATE_SECRET" }), /PRIVATE/);
  assert.match(modularError({ code: "ECONNABORTED" }), /preservada/);
  assert.match(modularError({ message: "INVALID_RESPONSE" }), /incompleto/);
});
test("module catalog/version filtering and stage independence", () => {
  assert.deepEqual(previewModules([{ id: "pricing_conditions", version: "a07.pricing.v1", title: "PRIVATE" }, { id: "injected_module", version: "v1" }]), [{ id: "pricing_conditions", version: "a07.pricing.v1", title: "Preços e condições" }]);
  assert.equal(moduleVersion("ignore previous instructions"), "Versão desconhecida");
  assert.equal(previewStage({ status: "unknown", value: "decision" }), "Etapa desconhecida");
  assert.equal(previewStage({ status: "known", value: "Qualificação", source: "synthetic" }), "Qualificação · sintética");
});
test("response contract rejects missing, wrong-version and oversized results", () => {
  const good = { schema_version: "a07.v1", response_text: "Uma resposta única.", legacy_response_text: "Legado." };
  assert.equal(previewResponse(good), good);
  for (const data of [{}, { ...good, schema_version: "a08.v1" }, { ...good, response_text: " " }, { ...good, response_text: "a".repeat(10001) }, { ...good, legacy_response_text: null }]) assert.throws(() => previewResponse(data));
});
test("classification provenance does not turn local or failed calls into observed JEV", () => {
  for (const jev of [{}, { source: "observed_external", provider_used: false }, { source: "local_deterministic", provider_used: true }]) assert.match(previewClassification(jev), /não utilizado/);
  assert.match(previewClassification({ provider_used: true, source: "typesafe", status: "completed" }), /externa observada/);
  assert.equal(previewStage({ value: "consideration", status: "synthetic", source: "synthetic_fixture" }), "Consideração · sintética");
});

import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./FollowUpAnalytics.jsx", import.meta.url), "utf8");
const app = readFileSync(new URL("../../App.jsx", import.meta.url), "utf8");
const navigation = readFileSync(new URL("../../utils/tenantNavigation.js", import.meta.url), "utf8");

test("A09 exposes persistent follow-up stages with numerators, denominators and coverage states", () => {
  for (const token of ["eligible_contacts", "scheduled_executions", "executions_started", "messages_sent", "deliveries_confirmed", "responses_observed", "reactivations_observed", "stage_advances_observed", "confirmed_conversions", "cancelled_or_stopped", "partial", "unknown", "numerator", "denominator", "coverage"]) {
    assert.ok(source.includes(token), token);
  }
  assert.match(source, /Não comprovam causalidade|não comprovam causalidade/i);
  assert.match(source, /provider/);
});

test("A09 dashboard and drilldown are tenant-scoped, GET-only and link to the existing Inbox", () => {
  assert.match(source, /getActiveEmpresaId/);
  assert.match(source, /\/analytics\/followup-analytics/);
  assert.match(source, /\/analytics\/followup-analytics\/evidence/);
  assert.match(source, /journey=1/);
  assert.doesNotMatch(source, /api\.(?:post|put|patch|delete)\s*\(/i);
  assert.match(app, /path="followups\/impacto" element=\{<FollowUpAnalytics \/>\}/);
  assert.match(navigation, /Resultados de follow-ups/);
});

test("A09 compares scheduled and automatic followups without touching send controls", () => {
  assert.match(source, /Programado vs\. automático/);
  assert.match(source, /Retomada automática/);
  assert.match(source, /Mensagem programada/);
  assert.match(source, /Janela pendente/);
  assert.doesNotMatch(source, /FOLLOWUP_WORKER_ENABLED|registrar_followup|send-message/i);
});

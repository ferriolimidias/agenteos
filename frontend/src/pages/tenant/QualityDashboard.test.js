import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";

const source = readFileSync(new URL("./QualityDashboard.jsx", import.meta.url), "utf8");

test("A08 exposes objective quality states, denominators and coverage", () => {
  for (const token of ["question_without_observed_response", "price_offer_without_observed_continuity", "response_coverage", "observed", "partial", "unknown", "not_applicable", "numerator", "denominator", "coverage"]) assert.ok(source.includes(token), token);
  assert.match(source, /não são retornados|sem texto|sem dados de contato/i);
});

test("A08 filters and evidence navigation stay tenant scoped and read-only", () => {
  for (const token of ["ad_id", "offer_signal", "stage", "composition_version", "/analytics/attendance-quality", "/analytics/attendance-quality/evidence", "journey=1"]) assert.ok(source.includes(token), token);
  assert.doesNotMatch(source, /api\.(?:post|put|patch|delete)\s*\(/i);
  assert.match(source, /AbortController/);
});

test("A08 explicitly separates hypotheses from facts and keeps traces descriptive", () => {
  assert.match(source, /Hipótese/);
  assert.match(source, /Fato observado/);
  assert.match(source, /Traces agregados/);
  assert.match(source, /versão exata do prompt/);
  assert.match(source, /não é diagnóstico de intenção/);
  assert.match(source, /Etapa — \{data\.filter_scope\?\.stage\}/);
  assert.match(source, /Anúncio — \{data\.filter_scope\?\.ad\}/);
  assert.match(source, /Composição — \{data\.filter_scope\?\.composition_version\}/);
  assert.match(source, /mature_inbound_denominator/);
  assert.match(source, /input_token_samples/);
  assert.match(source, /cost_samples/);
  assert.match(source, /unknown_channel_messages/);
  assert.match(source, /filter_scope\?\.channel/);
});

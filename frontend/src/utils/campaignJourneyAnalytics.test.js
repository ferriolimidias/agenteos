import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";

import { a10Rate, a10StateLabel, formatA10Metric } from "./campaignJourneyAnalytics.js";

const component = readFileSync(new URL("../pages/tenant/CampaignJourneyAnalytics.jsx", import.meta.url), "utf8");
const campaignCentral = readFileSync(new URL("../pages/tenant/Campanhas.jsx", import.meta.url), "utf8");

test("A10 rates always show numerator and denominator and preserve an observed zero", () => {
  assert.equal(formatA10Metric({ value: 0, numerator: 0, denominator: 4 }), "0% · 0 / 4");
  assert.equal(a10Rate({ value: 0.25, numerator: 1, denominator: 4 }), "25% · 1 / 4");
});

test("A10 distinguishes unknown and not applicable from observed zero", () => {
  assert.equal(a10Rate({ value: null, state: "unknown" }), "Desconhecido");
  assert.equal(a10Rate({ value: null, state: "not_applicable" }), "Não aplicável");
  assert.equal(a10StateLabel("partial"), "Parcial");
});

test("A10 uses read-only campaign journey and recipient drill-down GET routes", () => {
  assert.match(component, /campaign-central\/journey`/);
  assert.match(component, /campaign-central\/journey\/recipients`/);
  assert.doesNotMatch(component, /api\.(post|put|patch|delete)\(/);
  assert.match(component, /journey=1&lead=/);
});

test("A10 panel is integrated into Campaign Central and warns against causal claims", () => {
  assert.match(campaignCentral, /label: "Jornada A10"/);
  assert.match(campaignCentral, /<CampaignJourneyAnalytics campaigns=\{campaigns\}/);
  assert.match(component, /correlação temporal/);
  assert.match(component, /não causalidade/);
  assert.match(component, /Até 5|até 5/);
  assert.match(campaignCentral, /activeTab !== "journey"/);
  assert.match(campaignCentral, /Análise somente leitura/);
});

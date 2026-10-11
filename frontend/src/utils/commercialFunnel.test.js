import test from "node:test";
import assert from "node:assert/strict";
import { aggregateCsvIsSafe, allowlistedFunnelFilters, defaultFunnelFilters, durationLabel, funnelCount, funnelError, funnelParams, funnelPercent, funnelRate, funnelRevenue, funnelTime, inputUtcTime, journeyInboxPath, journeyLeadFromSearch, utcInputValue } from "./commercialFunnel.js";
test("default cohort spans 90 days UTC, never future and has explicit inactivity/return thresholds", () => {
  const filters = defaultFunnelFilters(new Date("2026-10-11T12:13:14Z"));
  assert.equal(filters.end, "2026-10-11T12:13:14.000Z"); assert.equal(filters.start, "2026-07-13T12:13:14.000Z");
  assert.equal(filters.inactivity_hours, 168); assert.equal(filters.return_window_days, 7); assert.equal(filters.observed_at, undefined);
});
test("query filters exclude arbitrary PII/redirect/objects and preserve the observation horizon between pages", () => {
  const filters = { start: "2026-08-01T00:00:00Z", observed_at: "2026-10-11T12:00:00Z", stage: "qualified", phone: "PRIVATE", next: "https://outside.test", status: { nested: true }, campaign_id: "", return_window_days: 7 };
  assert.deepEqual(allowlistedFunnelFilters(filters), { start: filters.start, observed_at: filters.observed_at, stage: "qualified", campaign_id: "", return_window_days: 7 });
  assert.deepEqual(funnelParams(filters, { offset: 25, limit: 25 }), { start: filters.start, observed_at: filters.observed_at, stage: "qualified", return_window_days: 7, offset: 25, limit: 25 });
});
test("missing counts/rates/revenue remain unknown, measured zero remains zero, denominators explicit", () => {
  assert.equal(funnelCount(null), "Não registrado"); assert.equal(funnelCount(0), "0"); assert.equal(funnelPercent(null), "Desconhecida"); assert.equal(funnelPercent(0), "0%");
  assert.equal(funnelRate({ value: 0.25, numerator: 2, denominator: 8 }), "25% · 2 / 8");
  assert.match(funnelRevenue({ status: "unknown", by_currency: [] }), /desconhecida/);
  assert.match(funnelRevenue({ by_currency: [{ currency: "BRL", amount: null }] }), /desconhecida/);
  assert.equal(funnelRevenue({ by_currency: [{ currency: "BRL", amount: 0 }, { currency: "USD", amount: 13.99 }] }), "BRL 0,00 · USD 13,99");
});
test("A04 deeplink requires a UUID and explicit journey flag, never trusts API redirect or phone", () => {
  const id = "00000000-0000-0000-0000-000000000002";
  assert.equal(journeyInboxPath(id), `/painel/inbox?lead=${id}&journey=1`);
  assert.equal(journeyLeadFromSearch(`?lead=${id}&journey=1&next=https://outside.test`), id);
  for (const value of [null, "5551999999999", "https://outside.test", "../other"]) assert.equal(journeyInboxPath(value), null);
  assert.equal(journeyLeadFromSearch(`?lead=${id}`), null);
});
test("UTC controls and displayed time preserve timezone and missing duration is not invented", () => {
  assert.equal(inputUtcTime("2026-08-01T03:00:00-03:00"), "2026-08-01T06:00"); assert.equal(utcInputValue("2026-08-01T00:00"), "2026-08-01T00:00:00.000Z");
  assert.match(funnelTime("2026-08-01T03:00:00Z"), /03:00/); assert.equal(funnelTime(null), "Não registrado"); assert.equal(durationLabel(null), "Não registrado");
  assert.match(durationLabel({ median: 25, max: 50 }), /Mediana 25 h · máximo 50 h/);
});
test("disabled and error states do not claim there are no leads or expose server details", () => {
  assert.match(funnelError({ response: { status: 404 } }), /Inbox continuam/); assert.match(funnelError({ response: { status: 403 } }), /tenant/); assert.match(funnelError({ response: { status: 422 } }), /366 dias/);
  assert.ok(!funnelError({ response: { status: 500, data: { detail: "PRIVATE" } } }).includes("PRIVATE"));
});

test("CSV export rejects arbitrary schemas and PII instead of saving a contact export", () => {
  const safe = "section,metric,value,numerator,denominator,state,currency\nsummary,total_leads,10,,10,observed,\n";
  assert.equal(aggregateCsvIsSafe(safe), true);
  for (const unsafe of ["lead_id,display_name\nprivate,Customer", safe + "revenue,email,person@example.org", safe + "stage,key,10000000-0000-0000-0000-000000000001"]) assert.equal(aggregateCsvIsSafe(unsafe), false);
});

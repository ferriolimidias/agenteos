import test from "node:test";
import assert from "node:assert/strict";
import { operationsParams, outcomePresentation, emptyObservation, operationsError } from "./conversionOperations.js";

test("preserves unknown delivery as ambiguous rather than accepted or retryable", () => {
  assert.equal(outcomePresentation("unknown").label, "Entrega desconhecida");
  assert.equal(outcomePresentation("accepted").label, "Aceito pela Meta");
  assert.equal(outcomePresentation("DELIVERED").tone, "warning");
});
test("global absence of observations differs from an empty filtered list", () => {
  assert.equal(emptyObservation({ observation_status: "NO_EVENT_OBSERVED" }), true);
  assert.equal(emptyObservation({ observation_status: "EVENT_OBSERVED", total_attempts: 1 }), false);
  assert.equal(emptyObservation(undefined), false);
});
test("sends only supported nonempty filters and pagination", () => {
  assert.deepEqual(operationsParams({ event_name: " Purchase ", outcome: "unknown", transport: "capi", extra: "ignored" }, 20), { limit: 20, offset: 20, event_name: "Purchase", outcome: "unknown", transport: "capi" });
  assert.deepEqual(operationsParams({}), { limit: 20, offset: 0 });
});
test("dates become precise ISO dates and invalid intervals are rejected", () => {
  assert.equal(operationsParams({ start_at: "2026-10-10T12:00:00Z" }).start_at, "2026-10-10T12:00:00.000Z");
  assert.throws(() => operationsParams({ start_at: "bad" }), /data válida/);
  assert.throws(() => operationsParams({ start_at: "2026-10-11", end_at: "2026-10-10" }), /anterior/);
});
test("API errors do not masquerade as no natural event or leak response text", () => {
  assert.match(operationsError({ response: { status: 404 } }), /não indica ausência/);
  assert.match(operationsError({ response: { status: 403 } }), /não tem acesso/);
  assert.match(operationsError({ response: { status: 400 } }), /filtros não são válidos/);
  assert.match(operationsError({ response: { status: 422 } }), /filtros não são válidos/);
  assert.match(operationsError({ response: { status: 500, data: { detail: "private token" } } }), /não pode ser confirmado/);
  assert.ok(!operationsError({ response: { status: 500, data: { detail: "private token" } } }).includes("private token"));
});

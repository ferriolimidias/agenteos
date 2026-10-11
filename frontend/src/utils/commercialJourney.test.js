import test from "node:test";
import assert from "node:assert/strict";
import { journeyState, journeyTime, journeyError, mergeJourney, safeJourneyDetails } from "./commercialJourney.js";
test("missing evidence stays unknown rather than explicit non-occurrence or abandonment", () => {
  assert.match(journeyState("unknown").label, /não observado/);
  assert.match(journeyState("not_occurred").label, /evidência explícita/);
  assert.match(journeyState("pending").label, /andamento/);
  assert.equal(journeyState("abandoned").tone, "warning");
});
test("cursor append deduplicates exact IDs while preserving distinct same-time facts and order", () => {
  const first = [{ id: "message:1", occurred_at: "2026-10-11T12:00:00Z" }];
  const next = [first[0], { id: "commercial:1", occurred_at: first[0].occurred_at }];
  assert.deepEqual(mergeJourney(first, next).map(item => item.id), ["message:1", "commercial:1"]);
});
test("timestamps use explicit UTC and absence does not invent a date", () => {
  assert.match(journeyTime("2026-10-11T12:34:56Z", "UTC"), /12:34:56/);
  assert.equal(journeyTime(null), "Timestamp não disponível");
  assert.equal(journeyTime("invalid"), "Timestamp não disponível");
});
test("details omit payloads, credentials, arbitrary PII and nested objects", () => {
  const result = safeJourneyDetails({ event_name: "Purchase", verified: true, currency: "BRL", token: "private", telefone: "private", payload: { raw: "private" }, status: { nested: "private" } });
  assert.deepEqual(result.map(row => row.key), ["event_name", "currency", "verified"]);
  assert.equal(result.find(row => row.key === "verified").value, "sim");
  assert.ok(!JSON.stringify(result).includes("private"));
});
test("disabled/error state is distinct from zero observed facts without exposing API detail", () => {
  assert.match(journeyError({ response: { status: 404 } }), /conversa continua disponível/);
  assert.match(journeyError({ response: { status: 503, data: { detail: "secret" } } }), /não significa ausência/);
});

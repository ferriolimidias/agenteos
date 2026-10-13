import test from "node:test";
import assert from "node:assert/strict";
import process from "node:process";
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
test("timestamps default to Brasília and technical UTC preserves the same instant", () => {
  assert.match(journeyTime("2026-10-11T12:34:56Z"), /09:34:56/);
  assert.match(journeyTime("2026-10-11T12:34:56Z", "UTC"), /12:34:56/);
  assert.equal(journeyTime("2026-10-11T12:34:56Z", "local"), journeyTime("2026-10-11T12:34:56Z"));
});
test("missing and malformed timestamps do not invent a date", () => {
  assert.equal(journeyTime(null), "Timestamp não disponível");
  assert.equal(journeyTime(undefined), "Timestamp não disponível");
  assert.equal(journeyTime(""), "Timestamp não disponível");
  assert.equal(journeyTime("invalid"), "Timestamp não disponível");
});
test("Brasília day boundaries and offset-equivalent instants are displayed consistently", () => {
  const utc = "2026-10-11T01:30:45Z";
  const offset = "2026-10-10T22:30:45-03:00";
  assert.match(journeyTime(utc), /10\/10\/2026.*22:30:45/);
  assert.equal(journeyTime(utc), journeyTime(offset));
  assert.match(journeyTime("2026-10-11T03:00:00Z"), /11\/10\/2026.*00:00:00/);
});
test("historical Brasília DST gaps and repeated hours use the instant's historical offset", () => {
  assert.match(journeyTime("2018-11-04T02:30:00Z"), /03\/11\/2018.*23:30:00/);
  assert.match(journeyTime("2018-11-04T03:30:00Z"), /04\/11\/2018.*01:30:00/);
  assert.match(journeyTime("2019-02-17T01:30:00Z"), /16\/02\/2019.*23:30:00/);
  assert.equal(journeyTime("2019-02-17T01:30:00Z"), journeyTime("2019-02-17T02:30:00Z"));
});
test("formatting in a foreign host timezone keeps Brasília and original facts unchanged", () => {
  const previousZone = process.env.TZ;
  const facts = [{ id: "booking:1", occurred_at: "2019-02-17T02:30:00Z" }, { id: "booking:2", occurred_at: "2019-02-17T01:30:00Z" }];
  const snapshot = JSON.stringify(facts);
  try {
    process.env.TZ = "Asia/Tokyo";
    assert.match(journeyTime("2026-10-11T01:30:00Z"), /10\/10\/2026.*22:30:00/);
    assert.equal(journeyTime(facts[0].occurred_at), journeyTime(facts[1].occurred_at));
    assert.equal(JSON.stringify(facts), snapshot);
    assert.deepEqual(mergeJourney(facts, facts).map(item => item.id), ["booking:1", "booking:2"]);
  } finally {
    if (previousZone === undefined) delete process.env.TZ;
    else process.env.TZ = previousZone;
  }
});
test("details omit payloads, credentials, arbitrary PII and nested objects", () => {
  const result = safeJourneyDetails({ event_name: "Purchase", verified: true, currency: "BRL", token: "private", telefone: "private", payload: { raw: "private" }, status: { nested: "private" } });
  assert.deepEqual(result.map(row => row.key), ["event_name", "currency", "verified"]);
  assert.equal(result.find(row => row.key === "verified").value, "sim");
  assert.ok(!JSON.stringify(result).includes("private"));
});
test("temporal evidence displays Brasília while retaining original allowlisted timestamps", () => {
  const details = { started_at: "2026-10-11T01:30:00Z", scheduled_at: "2018-11-04T03:30:00+00:00", ended_at: "invalid", token: "private", email: "private" };
  const original = JSON.stringify(details);
  const rows = safeJourneyDetails(details);
  assert.match(rows.find(row => row.key === "started_at").value, /10\/10\/2026.*22:30:00/);
  const scheduled = rows.find(row => row.key === "scheduled_at");
  assert.match(scheduled.value, /04\/11\/2018.*01:30:00/);
  assert.equal(scheduled.original, details.scheduled_at);
  assert.equal(rows.find(row => row.key === "ended_at").value, "Timestamp não disponível");
  assert.equal(rows.find(row => row.key === "ended_at").original, undefined);
  assert.ok(!JSON.stringify(rows).includes("private"));
  assert.equal(JSON.stringify(details), original);
});
test("long-history pagination keeps API chronology, exact IDs and source timestamps", () => {
  const items = Array.from({ length: 61 }, (_, index) => ({ id: `message:${index}`, occurred_at: new Date(Date.UTC(2026, 9, 11, 12, 0, -index)).toISOString() }));
  const pages = mergeJourney(items.slice(0, 30), items.slice(29, 60));
  const all = mergeJourney(pages, items.slice(59));
  assert.deepEqual(all, items);
  assert.equal(new Set(all.map(item => item.id)).size, 61);
});
test("disabled/error state is distinct from zero observed facts without exposing API detail", () => {
  assert.match(journeyError({ response: { status: 404 } }), /conversa continua disponível/);
  assert.match(journeyError({ response: { status: 503, data: { detail: "secret" } } }), /não significa ausência/);
});

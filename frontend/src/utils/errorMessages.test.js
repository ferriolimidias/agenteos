import assert from "node:assert/strict";
import test from "node:test";
import { normalizeApiError } from "./errorMessages.js";

test("normaliza detail objeto sem produzir [object Object]", () => {
  const value = normalizeApiError({ response: { data: { detail: { code: "INVALID", message: "Token inválido." } } } });
  assert.equal(value, "Token inválido.");
  assert.equal(value.includes("[object Object]"), false);
});

test("normaliza erros de validação em lista", () => {
  assert.equal(
    normalizeApiError({ response: { data: { detail: [{ loc: ["body", "token"], msg: "campo obrigatório" }] } } }),
    "body.token: campo obrigatório",
  );
});

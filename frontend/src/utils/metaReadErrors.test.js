import test from "node:test";
import assert from "node:assert/strict";
import { describeMetaReadError } from "./metaReadErrors.js";

test("descreve falhas de autorização sem expor a resposta bruta", () => {
  assert.deepEqual(describeMetaReadError({ response: { status: 401, data: { detail: "private token text" } } }), {
    message: "Sua sessão ou autorização expirou. Atualize o acesso e tente novamente.", retryable: false,
  });
  assert.match(describeMetaReadError({ response: { status: 403, data: { detail: { code: "META_READ_PERMISSION_REQUIRED" } } } }).message, /não tem permissão/);
  assert.match(describeMetaReadError({ response: { status: 400, data: { detail: { code: "missing_permission" } } } }).message, /não tem permissão/);
  assert.doesNotMatch(describeMetaReadError({ response: { status: 403, data: { detail: "private token text" } } }).message, /private token/);
});

test("marca rate limit, timeout, indisponibilidade e rede como retryable", () => {
  for (const error of [
    { response: { status: 429 } },
    { response: { status: 400, data: { detail: { code: "rate_limit" } } } },
    { response: { status: 400, data: { detail: { code: "timeout" } } } },
    { response: { status: 504, data: { detail: { code: "META_READ_TIMEOUT" } } } },
    { response: { status: 503, data: { detail: { code: "META_READ_TEMPORARILY_UNAVAILABLE" } } } },
    new Error("offline"),
  ]) assert.equal(describeMetaReadError(error).retryable, true);
});

test("preserva estados não retryable de feature desligada e configuração", () => {
  assert.equal(describeMetaReadError({ response: { status: 503, data: { detail: { code: "META_EXISTING_POST_ADS_DISABLED" } } } }).retryable, false);
  assert.equal(describeMetaReadError({ response: { status: 409 } }).retryable, false);
});

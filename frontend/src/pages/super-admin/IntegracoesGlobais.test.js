import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const source = readFileSync(new URL("./IntegracoesGlobais.jsx", import.meta.url), "utf8");

test("global integrations query a single selected tenant only after an explicit read action", () => {
  assert.match(source, /onClick=\{inspectCompany\}/);
  assert.match(source, /setSelectedCompanyId\(company\.id\)/);
  assert.match(source, /traffic\/meta\/credentials\/status/);
  assert.match(source, /instagram\/connections/);
  assert.match(source, /Promise\.all\(/);
  assert.match(source, /não grava configuração/);
  assert.doesNotMatch(source, /access_token|api_key|Authorization/);
});

test("integration readiness keeps configured assets, permissions, provider reads, and campaign-write readiness distinct", () => {
  assert.match(source, /Credencial presente/);
  assert.match(source, /Leitura validada/);
  assert.match(source, /ads_read concedido/);
  assert.match(source, /Vínculo configurado/);
  assert.match(source, /Ver readiness na área de campanhas/);
  assert.match(source, /não comprova permissão de escrita/);
});

test("platform integrations expose shared configuration status separately from company credentials", () => {
  assert.match(source, /admin\/integrations\/global-status/);
  assert.match(source, /Integrações globais da plataforma/);
  assert.match(source, /credenciais Marketing, Instagram e JEV continuam no escopo de cada empresa/);
  assert.match(source, /Escrita Marketing bloqueada/);
});

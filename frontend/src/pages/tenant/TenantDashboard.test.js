import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

const dashboardSource = readFileSync(new URL("./TenantDashboard.jsx", import.meta.url), "utf8");

test("dashboard shows WhatsApp channel instead of legacy robot status", () => {
  assert.match(dashboardSource, /Canal WhatsApp/);
  assert.match(dashboardSource, /Agente de atendimento/);
  assert.match(dashboardSource, /Conexão com IA/);
  assert.doesNotMatch(dashboardSource, /Status do robô/);
});

test("dashboard translates provider and connection states for users", () => {
  assert.match(dashboardSource, /API Oficial da Meta/);
  assert.match(dashboardSource, /Evolution - legado/);
  assert.match(dashboardSource, /Não configurado/);
  assert.match(dashboardSource, /Conectado/);
  assert.doesNotMatch(dashboardSource, /Offline.*DISCONNECTED/);
});

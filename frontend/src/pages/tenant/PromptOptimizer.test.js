import assert from "node:assert/strict";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import test from "node:test";

const frontendRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../../..");
const read = (relativePath) => fs.readFileSync(path.join(frontendRoot, relativePath), "utf8");

test("A11 page exposes versions, evidence coverage, and explicit offline/live states", () => {
  const page = read("src/pages/tenant/PromptOptimizer.jsx");
  assert.match(page, /Módulos e versões imutáveis/);
  assert.match(page, /Verificar estrutura \(local\)/);
  assert.match(page, /não aprova o comportamento do modelo/);
  assert.match(page, /approval_eligible/);
  assert.match(page, /nove casos sintéticos/);
  assert.match(page, /numerator/);
  assert.match(page, /denominator/);
  assert.match(page, /unknown/);
  assert.match(page, /Histórico parcial/);
  assert.match(page, /não causalidade|não causalidade|não causal/);
  assert.match(page, /A\/B LIVE está OFF/);
  assert.match(page, /Evidências por anúncio/);
  assert.match(page, /CTWA referral persistido/);
  assert.match(page, /Resultado temporal; não prova causalidade/);
  assert.match(page, /\/pause/);
  assert.match(page, /\/complete/);
  assert.match(page, /\/rollback/);
});

test("A11 frontend has no customer message dispatch or campaign side-effect call", () => {
  const page = read("src/pages/tenant/PromptOptimizer.jsx");
  assert.doesNotMatch(page, /api\.(post|put)\([^\n]*(\/send|campaigns\/[^`]*\/start|followups\/[^`]*\/send)/i);
  assert.match(page, /Dados sintéticos e previews não são contados/);
});

test("Prompt Optimizer is reachable only from company-scoped Super Admin routes, not the client menu", () => {
  const app = read("src/App.jsx");
  const navigation = read("src/utils/tenantNavigation.js");
  const backend = fs.readFileSync(path.resolve(frontendRoot, "../app/api/routers/prompt_optimizer.py"), "utf8");
  assert.match(app, /path="empresas\/:empresaId\/atendimento-ia"/);
  assert.match(app, /path="otimizador-prompts" element={<AcessoRestritoOperacional/);
  assert.doesNotMatch(navigation, /prompt-optimizer|Prompt Optimizer|SHADOW|A11/);
  assert.match(backend, /dependencies=\[Depends\(require_tenant_access\), Depends\(require_super_admin\)\]/);
  assert.doesNotMatch(backend, /is_admin_empresa_role/);
});

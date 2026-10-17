import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { TENANT_NAVIGATION_GROUPS, REVIEWER_NAVIGATION_GROUPS, getTenantNavigationGroups, resolveTenantNavigation, isTenantInboxPath, navigationPreferenceKey, readCollapsedGroups, saveCollapsedGroups, visibleCollapsedGroups } from "./tenantNavigation.js";

const app = readFileSync(new URL("../App.jsx", import.meta.url), "utf8");
const login = readFileSync(new URL("../pages/Login.jsx", import.meta.url), "utf8");
const adminLayout = readFileSync(new URL("../layouts/SuperAdminLayout.jsx", import.meta.url), "utf8");
const tenantRoutes = app.split('{/* Rota do Cliente (Tenant) */}')[1];
const routePatterns = new Set([...tenantRoutes.matchAll(/<Route path="([^"]+)"/g)].map((match) => match[1]));
const entries = TENANT_NAVIGATION_GROUPS.flatMap((group) => group.items);
const role = "admin_empresa";

function storage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), values };
}

test("client navigation has six task groups and keeps the Inbox first and expanded", () => {
  assert.deepEqual(TENANT_NAVIGATION_GROUPS.map((group) => group.label), ["Conversas", "Clientes", "Agenda", "Campanhas", "Resultados", "Configurações"]);
  assert.equal(TENANT_NAVIGATION_GROUPS.length, 6);
  assert.equal(TENANT_NAVIGATION_GROUPS[0].items[0].id, "inbox");
  assert.equal(TENANT_NAVIGATION_GROUPS[0].items[0].to, "/painel/inbox");
  assert.deepEqual(readCollapsedGroups(storage(), "tenant", TENANT_NAVIGATION_GROUPS), ["customers", "schedule", "campaigns", "results", "settings"]);
  for (const entry of entries) assert.ok(!/prompt|shadow|a11|token|diagn[oó]stico|integra[cç][oõ]es t[eé]cnicas/i.test(`${entry.id} ${entry.label}`));
});

test("commercial customer routes resolve within their intended group", () => {
  const expected = [
    ["/painel/inbox", "inbox", "conversations"], ["/painel/transferencias", "transfers", "conversations"],
    ["/painel/crm", "crm", "customers"], ["/painel/funil", "funnel", "customers"], ["/painel/tags", "tags", "customers"],
    ["/painel/agenda", "agenda", "schedule"], ["/painel/trafego/meta", "traffic", "campaigns"],
    ["/painel/instagram", "instagram", "campaigns"], ["/painel/campanhas", "campaigns", "campaigns"],
    ["/painel/followups", "followups", "campaigns"], ["/painel/campanhas/templates", "templates", "campaigns"],
    ["/painel/campanhas/publicos", "audiences", "campaigns"], ["/painel/resultados", "dashboard", "results"],
    ["/painel/qualidade", "quality", "results"], ["/painel/followups/impacto", "followup-analytics", "results"],
    ["/painel/campanhas/resultados", "campaign-results", "results"], ["/painel/campanhas/resultados/custos", "costs", "results"],
    ["/painel/campanhas/resultados/conversoes", "campaign-conversions", "results"], ["/painel/conexoes", "connections", "settings"],
    ["/painel/rag", "knowledge", "settings"], ["/painel/unidades", "account", "settings"],
  ];
  for (const [path, id, groupId] of expected) {
    const resolved = resolveTenantNavigation(path, role);
    assert.equal(resolved?.item.id, id, path);
    assert.equal(resolved?.group.id, groupId, path);
  }
});

test("main dashboard lives in Results and the old panel URL resolves to Inbox", () => {
  assert.match(tenantRoutes, /<Route index element={<TenantHomeRedirect\s*\/>}/);
  assert.equal(routePatterns.has("resultados"), true);
  assert.equal(resolveTenantNavigation("/painel/", role).item.id, "inbox");
  assert.equal(resolveTenantNavigation("/painel?lead_id=synthetic", role).item.id, "inbox");
  assert.match(app, /function TenantHomeRedirect\(\)/);
  assert.match(app, /to="\/painel\/inbox"/);
  assert.match(app, /path="resultados" element={<TenantDashboard \/>}/);
  assert.match(login, /navigate\("\/painel\/inbox"\)/);
});

test("Super Admin shell rejects customer and reviewer roles before loading administrative data", () => {
  assert.match(adminLayout, /const isSuperAdmin = user\?\.role === "super_admin"/);
  assert.match(adminLayout, /if \(!isSuperAdmin\)[\s\S]*?<Navigate to=\{user \? "\/painel\/inbox" : "\/"\}/);
  assert.match(adminLayout, /if \(!isSuperAdmin\) return;/);
});

test("Inbox aliases and historic campaign links remain query-safe and share modules", () => {
  for (const path of ["/painel/chat", "/painel/inbox/", "/painel/conversas/?lead_id=synthetic#timeline"]) {
    assert.equal(resolveTenantNavigation(path, role).item.id, "inbox");
    assert.ok(isTenantInboxPath(path));
  }
  assert.equal(isTenantInboxPath("/painel/chat/unregistered"), false);
  const cases = [
    ["/painel/campanhas/nova", "campaigns"], ["/painel/campanhas/synthetic-id", "campaigns"],
    ["/painel/campanhas/templates/novo", "templates"], ["/painel/campanhas/templates/synthetic-id/", "templates"],
    ["/painel/campanhas/publicos", "audiences"], ["/painel/campanhas/resultados", "campaign-results"],
    ["/painel/campanhas/resultados/custos", "costs"], ["/painel/campanhas/resultados/conversoes", "campaign-conversions"],
    ["/painel/templates-whatsapp", "templates"], ["/painel/dashboard", "dashboard"], ["/painel/resultados/resumo", "dashboard"],
  ];
  for (const [path, id] of cases) assert.equal(resolveTenantNavigation(path, role)?.item.id, id, path);
  assert.equal(resolveTenantNavigation("/painel/campanhas/nova", role).detail, "Nova campanha");
  assert.equal(resolveTenantNavigation("/painel/campanhas/templates/novo", role).detail, "Novo modelo");
});

test("technical customer URLs are deliberately outside navigation and are handled by restricted or safe routes", () => {
  for (const path of ["/painel/otimizador-prompts", "/painel/inteligencia", "/painel/trafego/meta/conversoes", "/painel/integracoes", "/painel/conversoes"]) {
    assert.equal(resolveTenantNavigation(path, role), null, path);
  }
  assert.match(app, /path="otimizador-prompts" element={<AcessoRestritoOperacional/);
  assert.match(app, /path="inteligencia" element={<AcessoRestritoOperacional/);
  assert.match(app, /path="agentes" element={<PreserveLocationNavigate to="\/painel\/inbox"/);
});

test("reviewer navigation stays isolated; normal customer roles share the six task groups", () => {
  const reviewer = "meta_app_reviewer";
  assert.deepEqual(getTenantNavigationGroups(reviewer), REVIEWER_NAVIGATION_GROUPS);
  assert.deepEqual(REVIEWER_NAVIGATION_GROUPS.flatMap((group) => group.items.map((entry) => entry.to)), ["/painel/conexoes", "/painel/tags"]);
  assert.equal(getTenantNavigationGroups("super_admin").length, 6);
  assert.equal(getTenantNavigationGroups("admin_empresa").length, 6);
  assert.equal(getTenantNavigationGroups("atendente").length, 6);
  assert.equal(resolveTenantNavigation("/painel/conexoes", reviewer)?.item.id, "connections");
  assert.equal(resolveTenantNavigation("/painel/inbox", reviewer), null);
});

test("visual preferences stay isolated and malformed storage cannot break the menu", () => {
  const first = navigationPreferenceKey("synthetic-tenant-1", role);
  const second = navigationPreferenceKey("synthetic-tenant-2", role);
  const review = navigationPreferenceKey("synthetic-tenant-1", "meta_app_reviewer");
  assert.notEqual(first, second);
  assert.notEqual(first, review);
  const store = storage();
  saveCollapsedGroups(store, first, ["customers", "customers", "unknown", { token: "never-store" }], TENANT_NAVIGATION_GROUPS);
  assert.deepEqual(JSON.parse(store.getItem(first)), { version: 1, collapsed: ["customers"] });
  assert.deepEqual(readCollapsedGroups(store, first, TENANT_NAVIGATION_GROUPS), ["customers"]);
  assert.deepEqual(readCollapsedGroups(store, review, REVIEWER_NAVIGATION_GROUPS), []);
  assert.deepEqual(readCollapsedGroups(storage({ [second]: "{malformed" }), second, TENANT_NAVIGATION_GROUPS), ["customers", "schedule", "campaigns", "results", "settings"]);
  const blocked = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("quota"); } };
  assert.deepEqual(readCollapsedGroups(blocked, first, TENANT_NAVIGATION_GROUPS), ["customers", "schedule", "campaigns", "results", "settings"]);
  assert.doesNotThrow(() => saveCollapsedGroups(blocked, first, [], TENANT_NAVIGATION_GROUPS));
});

test("active section opens on navigation unless the operator manually collapses it for this visit", () => {
  const collapsed = ["customers", "campaigns"];
  assert.deepEqual(visibleCollapsedGroups(collapsed, "customers", null, "direct"), ["campaigns"]);
  const manual = { groupId: "customers", visitKey: "current" };
  assert.deepEqual(visibleCollapsedGroups(collapsed, "customers", manual, "current"), collapsed);
  assert.deepEqual(visibleCollapsedGroups(collapsed, "customers", manual, "forward"), ["campaigns"]);
});

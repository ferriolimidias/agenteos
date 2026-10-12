import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import test from "node:test";
import { TENANT_NAVIGATION_GROUPS, REVIEWER_NAVIGATION_GROUPS, getTenantNavigationGroups, resolveTenantNavigation, isTenantInboxPath, normalizeTenantPath, navigationPreferenceKey, sanitizeCollapsedGroups, readCollapsedGroups, saveCollapsedGroups, visibleCollapsedGroups } from "./tenantNavigation.js";

const app = readFileSync(new URL("../App.jsx", import.meta.url), "utf8");
const tenantRoutes = app.split('{/* Rota do Cliente (Tenant) */}')[1];
const patterns = ["", ...[...tenantRoutes.matchAll(/<Route path="([^"]+)"/g)].map((match) => match[1]).filter((path) => path !== "/painel")];
const entries = TENANT_NAVIGATION_GROUPS.flatMap((group) => group.items);
const role = "tenant_admin";

function storage(initial = {}) {
  const values = new Map(Object.entries(initial));
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => values.set(key, value), values };
}

test("every declared Tenant route family resolves to a reachable module", () => {
  assert.equal(patterns.length, 32, "new routes need an explicit navigation audit");
  for (const pattern of patterns) {
    const concrete = `/painel${pattern ? `/${pattern.replace(/:id/g, "synthetic-id").replace(/\*/g, "legacy-detail")}` : ""}`;
    assert.ok(resolveTenantNavigation(concrete, role), `route missing from map: ${pattern || "index"}`);
  }
});

test("every navigation link targets a currently declared route and entries are unique", () => {
  const declared = new Set(patterns.map((pattern) => `/painel${pattern ? `/${pattern}` : ""}`));
  const wildcards = [...declared].filter((path) => path.endsWith("/*")).map((path) => path.slice(0, -2));
  for (const entry of entries) assert.ok(declared.has(entry.to) || wildcards.some((base) => entry.to === base || entry.to.startsWith(`${base}/`)), `${entry.id} has no App route`);
  assert.equal(new Set(entries.map((entry) => entry.id)).size, entries.length);
  assert.equal(new Set(entries.map((entry) => entry.to)).size, entries.length);
  assert.equal(TENANT_NAVIGATION_GROUPS.length, 5);
});

test("Inbox aliases, trailing slash and query/hash share exactly one module", () => {
  for (const path of ["/painel/chat", "/painel/inbox/", "/painel/conversas/?contato=synthetic#jornada"]) {
    assert.equal(resolveTenantNavigation(path, role).item.id, "inbox");
    assert.ok(isTenantInboxPath(path));
  }
  assert.equal(resolveTenantNavigation("/painel/", role).item.id, "dashboard");
  assert.equal(isTenantInboxPath("/painel/chat/unregistered"), false);
});

test("longest campaign match prevents overview/results/costs/conversions double activation", () => {
  const cases = [
    ["/painel/campanhas", "campaigns"], ["/painel/campanhas/synthetic-id", "campaigns"],
    ["/painel/campanhas/nova", "campaigns"], ["/painel/campanhas/templates", "templates"],
    ["/painel/campanhas/templates/novo", "templates"], ["/painel/campanhas/templates/synthetic-id/", "templates"],
    ["/painel/campanhas/publicos", "audiences"], ["/painel/campanhas/resultados", "results"],
    ["/painel/campanhas/resultados/custos", "costs"], ["/painel/campanhas/resultados/conversoes", "campaign-conversions"],
    ["/painel/templates-whatsapp", "templates"], ["/painel/trafego/meta", "traffic"],
    ["/painel/trafego/meta/conversoes", "meta-conversions"],
  ];
  for (const [path, id] of cases) assert.equal(resolveTenantNavigation(path, role).item.id, id, path);
});

test("context labels distinguish campaign/template actions without exposing route identifiers", () => {
  assert.equal(resolveTenantNavigation("/painel/campanhas/nova", role).detail, "Nova campanha");
  assert.equal(resolveTenantNavigation("/painel/campanhas/synthetic-private-id", role).detail, "Detalhes da campanha");
  assert.equal(resolveTenantNavigation("/painel/campanhas/templates/novo", role).detail, "Novo template");
  assert.equal(resolveTenantNavigation("/painel/campanhas/templates/synthetic-private-id", role).detail, "Detalhes do template");
  assert.equal(resolveTenantNavigation("/painel/funil", role).detail, null);
});

test("unknown URLs and prefix lookalikes do not activate unrelated tools", () => {
  for (const path of [null, "", "/admin", "/painel/unregistered", "/painel/funil-extra", "/painel/campanhas-extra", "/painel/trafego/meta-extra", "/painel/trafego/meta/conversoes-extra"]) {
    assert.equal(resolveTenantNavigation(path, role), null, String(path));
  }
  assert.equal(normalizeTenantPath(null), "/");
});

test("reviewer map exposes only the two existing review tools and never admin integrations", () => {
  const reviewer = "meta_app_reviewer";
  assert.deepEqual(getTenantNavigationGroups(reviewer), REVIEWER_NAVIGATION_GROUPS);
  assert.deepEqual(REVIEWER_NAVIGATION_GROUPS.flatMap((group) => group.items.map((entry) => entry.to)), ["/painel/conexoes", "/painel/tags"]);
  assert.ok(resolveTenantNavigation("/painel/conexoes", reviewer));
  assert.ok(resolveTenantNavigation("/painel/tags", reviewer));
  for (const entry of entries.filter((entry) => !["connections", "tags"].includes(entry.id))) assert.equal(resolveTenantNavigation(entry.to, reviewer), null);
  for (const group of TENANT_NAVIGATION_GROUPS) for (const entry of group.items) assert.ok(!entry.to.startsWith("/admin"));
});

test("visual preferences are isolated by tenant and role and persist only recognized group ids", () => {
  const first = navigationPreferenceKey("synthetic-tenant-1", role);
  const second = navigationPreferenceKey("synthetic-tenant-2", role);
  const review = navigationPreferenceKey("synthetic-tenant-1", "meta_app_reviewer");
  assert.notEqual(first, second);
  assert.notEqual(first, review);
  const store = storage();
  saveCollapsedGroups(store, first, ["attendance", "attendance", "unknown", { token: "never-store" }], TENANT_NAVIGATION_GROUPS);
  assert.deepEqual(JSON.parse(store.getItem(first)), { version: 1, collapsed: ["attendance"] });
  assert.deepEqual(readCollapsedGroups(store, first, TENANT_NAVIGATION_GROUPS), ["attendance"]);
  assert.deepEqual(readCollapsedGroups(store, review, REVIEWER_NAVIGATION_GROUPS), []);
  assert.deepEqual(readCollapsedGroups(store, second, TENANT_NAVIGATION_GROUPS), ["attendance", "acquisition", "automation", "settings"]);
});

test("malformed, outdated or blocked preference storage cannot break navigation", () => {
  const key = navigationPreferenceKey("synthetic", role);
  const defaults = ["attendance", "acquisition", "automation", "settings"];
  for (const value of ["{malformed", "null", "[]", JSON.stringify({ version: 9, collapsed: [] }), JSON.stringify({ version: 1, collapsed: "all" })]) {
    assert.deepEqual(readCollapsedGroups(storage({ [key]: value }), key, TENANT_NAVIGATION_GROUPS), defaults);
  }
  const blocked = { getItem() { throw new Error("blocked"); }, setItem() { throw new Error("quota"); } };
  assert.deepEqual(readCollapsedGroups(blocked, key, TENANT_NAVIGATION_GROUPS), defaults);
  assert.doesNotThrow(() => saveCollapsedGroups(blocked, key, defaults, TENANT_NAVIGATION_GROUPS));
  assert.doesNotThrow(() => saveCollapsedGroups(null, key, defaults, TENANT_NAVIGATION_GROUPS));
  assert.deepEqual(sanitizeCollapsedGroups(["reviewer", "automation", 42, null], REVIEWER_NAVIGATION_GROUPS), ["reviewer"]);
});

test("active group overrides persisted collapse on entry while manual collapse applies only to its visit", () => {
  const collapsed = ["attendance", "automation"];
  assert.deepEqual(visibleCollapsedGroups(collapsed, "attendance", null, "direct"), ["automation"]);
  const manual = { groupId: "attendance", visitKey: "current" };
  assert.deepEqual(visibleCollapsedGroups(collapsed, "attendance", manual, "current"), collapsed);
  assert.deepEqual(visibleCollapsedGroups(collapsed, "attendance", manual, "forward"), ["automation"]);
  assert.deepEqual(visibleCollapsedGroups(collapsed, "automation", manual, "current"), ["attendance"]);
});

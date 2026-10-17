import assert from "node:assert/strict";
import { test } from "node:test";
import { COMPANY_ADMIN_SECTIONS, companyAdminSectionHref, resolveCompanyAdminSection } from "./superAdminNavigation.js";

test("company administration exposes the eight requested contextual areas", () => {
  assert.deepEqual(COMPANY_ADMIN_SECTIONS.map(({ id }) => id), [
    "visao-geral", "atendimento-ia", "crm-regras", "canais", "campanhas-meta", "pagamentos", "usuarios", "diagnosticos",
  ]);
});

test("legacy tab links keep resolving into the new company areas", () => {
  assert.equal(resolveCompanyAdminSection({ search: "?tab=agente" }), "atendimento-ia");
  assert.equal(resolveCompanyAdminSection({ search: "?tab=regras" }), "crm-regras");
  assert.equal(resolveCompanyAdminSection({ search: "?tab=canais" }), "canais");
  assert.equal(resolveCompanyAdminSection({ search: "?tab=usuarios" }), "usuarios");
  assert.equal(resolveCompanyAdminSection({ search: "?tab=diagnostico" }), "diagnosticos");
});

test("specific company routes take precedence over legacy query parameters", () => {
  assert.equal(resolveCompanyAdminSection({ pathname: "/admin/empresas/acme/meta", search: "?tab=canais" }), "campanhas-meta");
  assert.equal(resolveCompanyAdminSection({ pathname: "/admin/empresas/acme/instagram" }), "canais");
  assert.equal(resolveCompanyAdminSection({ pathname: "/admin/empresas/acme/atendimento-ia/jev" }), "atendimento-ia");
});

test("section links retain the company context and encode the identifier", () => {
  assert.equal(companyAdminSectionHref("a/b", "usuarios"), "/admin/empresas/a%2Fb?area=usuarios");
  assert.equal(companyAdminSectionHref("acme", "atendimento-ia"), "/admin/empresas/acme?area=atendimento-ia");
  assert.equal(companyAdminSectionHref("acme", "unknown"), "/admin/empresas/acme");
});

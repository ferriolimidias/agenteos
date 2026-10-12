import test from "node:test";
import assert from "node:assert/strict";
import { companyStatus, filterCompanies, companyAreas } from "./adminCompanies.js";
const companies = [
  { id: "demo-2", nome_empresa: "Clínica Horizonte", area_atuacao: "Saúde", ativo: true },
  { id: "demo-1", nome_empresa: "Estúdio Aurora", area_atuacao: "Design", ativo: false },
  { id: "demo-3", nome_empresa: "Loja 10", area_atuacao: "Varejo" },
  { id: "demo-4", nome_empresa: "Loja 2", area_atuacao: "Varejo" },
];
test("busca combina nome, área e ID sem sensibilidade a acentos", () => {
  assert.deepEqual(filterCompanies(companies, {query:"clinica saude demo-2"}).map(x=>x.id), ["demo-2"]);
  assert.equal(filterCompanies(companies, {query:"ausente"}).length, 0);
});
test("filtros locais combinam sem mutar registros ou inferir estado", () => {
  assert.equal(filterCompanies(companies, {area:"Design", status:"inactive"})[0], companies[1]);
  assert.equal(filterCompanies(companies, {status:"unknown"}).length, 2);
  assert.equal(companyStatus({}).id, "unknown");
  assert.equal(companyStatus({ativo:"false"}).id, "unknown");
  assert.equal(companyStatus({ativo:false}).id, "inactive");
});
test("ordenação natural e áreas são determinísticas sem mutar entrada", () => {
  assert.deepEqual(filterCompanies(companies).map(x=>x.nome_empresa), ["Clínica Horizonte", "Estúdio Aurora", "Loja 2", "Loja 10"]);
  assert.deepEqual(companyAreas([...companies, companies[0]]), ["Design", "Saúde", "Varejo"]);
  assert.equal(companies[0].id, "demo-2");
});

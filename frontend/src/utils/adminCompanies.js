const normalize = value => String(value ?? "").normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLocaleLowerCase("pt-BR").trim();

// Listing status is metadata only. Missing values never become 'active'.
export function companyStatus(company) {
  if (company.ativo === true) return { id: "active", label: "Ativa", tone: "active" };
  if (company.ativo === false) return { id: "inactive", label: "Inativa", tone: "warning" };
  return { id: "unknown", label: "Não informado", tone: "default" };
}
export function filterCompanies(companies, { query = "", area = "", status = "", sort = "name" } = {}) {
  const terms = normalize(query).split(/\s+/).filter(Boolean);
  return companies.filter(company => {
    const text = normalize([company.nome_empresa, company.area_atuacao, company.id].join(" "));
    return terms.every(term => text.includes(term)) && (!area || company.area_atuacao === area) && (!status || companyStatus(company).id === status);
  }).sort((a, b) => String(sort === "area" ? a.area_atuacao || "" : a.nome_empresa || "").localeCompare(String(sort === "area" ? b.area_atuacao || "" : b.nome_empresa || ""), "pt-BR", { numeric: true }) || String(a.id).localeCompare(String(b.id)));
}
export function companyAreas(companies) {
  return [...new Set(companies.map(company => company.area_atuacao).filter(Boolean))].sort((a,b) => a.localeCompare(b, "pt-BR"));
}

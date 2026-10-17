export const COMPANY_ADMIN_SECTIONS = [
  { id: "visao-geral", label: "Visão geral", href: (id) => `/admin/empresas/${id}` },
  { id: "atendimento-ia", label: "Atendimento e IA", href: (id) => `/admin/empresas/${id}?area=atendimento-ia` },
  { id: "crm-regras", label: "CRM e regras comerciais", href: (id) => `/admin/empresas/${id}?area=crm-regras` },
  { id: "canais", label: "Canais e credenciais", href: (id) => `/admin/empresas/${id}?area=canais` },
  { id: "campanhas-meta", label: "Campanhas e Meta", href: (id) => `/admin/empresas/${id}/meta` },
  { id: "pagamentos", label: "Pagamentos", href: (id) => `/admin/empresas/${id}?area=pagamentos` },
  { id: "usuarios", label: "Usuários e permissões", href: (id) => `/admin/empresas/${id}?area=usuarios` },
  { id: "diagnosticos", label: "Diagnósticos e auditoria", href: (id) => `/admin/empresas/${id}?area=diagnosticos` },
];

const VALID_SECTION_IDS = new Set(COMPANY_ADMIN_SECTIONS.map(({ id }) => id));

const LEGACY_TAB_TO_SECTION = {
  "visao-geral": "visao-geral",
  agente: "atendimento-ia",
  conhecimento: "atendimento-ia",
  regras: "crm-regras",
  canais: "canais",
  pagamentos: "pagamentos",
  usuarios: "usuarios",
  diagnostico: "diagnosticos",
  "agenda-automacoes": "atendimento-ia",
  imagens: "atendimento-ia",
};

export function resolveCompanyAdminSection({ pathname = "", search = "" } = {}) {
  if (pathname.includes("/meta")) return "campanhas-meta";
  if (pathname.includes("/instagram")) return "canais";
  if (pathname.includes("/atendimento-ia")) return "atendimento-ia";

  const params = new URLSearchParams(search);
  const area = params.get("area");
  if (VALID_SECTION_IDS.has(area)) return area;

  const legacyTab = params.get("tab");
  if (legacyTab && LEGACY_TAB_TO_SECTION[legacyTab]) return LEGACY_TAB_TO_SECTION[legacyTab];
  return "visao-geral";
}

export function companyAdminSectionHref(companyId, sectionId) {
  const section = COMPANY_ADMIN_SECTIONS.find(({ id }) => id === sectionId);
  return section ? section.href(encodeURIComponent(String(companyId))) : COMPANY_ADMIN_SECTIONS[0].href(encodeURIComponent(String(companyId)));
}

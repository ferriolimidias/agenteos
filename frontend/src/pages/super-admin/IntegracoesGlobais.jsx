import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { Instagram, MessageCircle, RefreshCw, Search, Settings2, ShoppingBag } from "lucide-react";
import api from "../../services/api";
import { ActionButton, EmptyState, ErrorState, Input, LoadingState, PageHeader, SectionCard, StatusBadge } from "../../components/ui";

const readinessLabel = (value) => {
  const status = String(value || "").toUpperCase();
  if (["READY", "READY_TENANT_OAUTH", "READY_ADMIN_MANAGED_READ"].includes(status)) return { label: "Leitura validada", tone: "success" };
  if (status.includes("ERROR") || status.includes("INVALID")) return { label: "Falha na consulta", tone: "danger" };
  if (status) return { label: "Não confirmada", tone: "warning" };
  return { label: "Desconhecida", tone: "warning" };
};

function presence(value, configuredLabel, missingLabel) {
  if (value === true) return { label: configuredLabel, tone: "info" };
  if (value === false) return { label: missingLabel, tone: "warning" };
  return { label: "Não informado", tone: "warning" };
}

export default function IntegracoesGlobais() {
  const [companies, setCompanies] = useState([]);
  const [search, setSearch] = useState("");
  const [selectedCompanyId, setSelectedCompanyId] = useState("");
  const [integrationStatus, setIntegrationStatus] = useState(null);
  const [statusLoading, setStatusLoading] = useState(false);
  const [statusError, setStatusError] = useState("");
  const [statusUpdatedAt, setStatusUpdatedAt] = useState(null);
  const [globalStatus, setGlobalStatus] = useState(null);
  const [globalStatusLoading, setGlobalStatusLoading] = useState(true);
  const [globalStatusError, setGlobalStatusError] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const loadCompanies = useCallback(async () => {
    setLoading(true);
    setError(false);
    try {
      const response = await api.get("/empresas/");
      const rows = Array.isArray(response.data) ? response.data.map(({ id, nome_empresa }) => ({ id, nome_empresa })) : [];
      setCompanies(rows);
      setSelectedCompanyId((current) => rows.some((item) => item.id === current) ? current : "");
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { loadCompanies(); }, [loadCompanies]);

  const loadGlobalStatus = useCallback(async () => {
    setGlobalStatusLoading(true);
    setGlobalStatusError(false);
    try {
      const response = await api.get("/admin/integrations/global-status");
      setGlobalStatus(response.data || null);
    } catch {
      setGlobalStatusError(true);
    } finally {
      setGlobalStatusLoading(false);
    }
  }, []);

  useEffect(() => { loadGlobalStatus(); }, [loadGlobalStatus]);

  const visible = useMemo(() => {
    const query = search.trim().toLocaleLowerCase("pt-BR");
    return companies.filter((company) => `${company.nome_empresa || ""} ${company.id}`.toLocaleLowerCase("pt-BR").includes(query));
  }, [companies, search]);

  const inspectCompany = async () => {
    if (!selectedCompanyId || statusLoading) return;
    setStatusLoading(true);
    setStatusError("");
    setIntegrationStatus(null);
    const paths = {
      metaConfig: `/empresas/${selectedCompanyId}/traffic/meta/config`,
      metaCredentials: `/empresas/${selectedCompanyId}/traffic/meta/credentials/status`,
      instagram: `/empresas/${selectedCompanyId}/instagram/connections`,
    };
    const [metaConfig, metaCredentials, instagram] = await Promise.all(
      Object.entries(paths).map(async ([key, path]) => {
        try { return [key, { ok: true, data: (await api.get(path, { timeout: 15000 })).data }]; }
        catch { return [key, { ok: false }]; }
      }),
    );
    const data = Object.fromEntries([metaConfig, metaCredentials, instagram]);
    setIntegrationStatus(data);
    if (Object.values(data).some((item) => !item.ok)) setStatusError("Uma ou mais consultas não responderam. Os estados indisponíveis permanecem desconhecidos.");
    setStatusUpdatedAt(new Date());
    setStatusLoading(false);
  };

  const selectedCompany = companies.find((company) => company.id === selectedCompanyId);
  const metaConfig = integrationStatus?.metaConfig?.data;
  const metaCredential = integrationStatus?.metaCredentials?.data;
  const instagramConnections = integrationStatus?.instagram?.data?.connections;
  const metaReadiness = integrationStatus?.metaCredentials?.ok ? readinessLabel(metaCredential?.marketing_readiness_status || metaCredential?.status) : { label: "Não consultada ou indisponível", tone: "warning" };
  const tokenPresence = integrationStatus?.metaCredentials?.ok ? presence(metaCredential?.token_present, "Credencial presente", "Sem credencial") : { label: "Não informado", tone: "warning" };
  const permissions = metaCredential?.permissions || {};
  const adsRead = integrationStatus?.metaCredentials?.ok ? presence(metaCredential?.ads_read ?? permissions.ads_read, "ads_read concedido", "ads_read não concedido") : { label: "Não informado", tone: "warning" };
  const savedConfig = metaConfig?.config || {};

  return <div className="fm-admin-page">
    <PageHeader eyebrow="Plataforma Ferrioli" title="Integrações" icon={Settings2} description="Consulte credenciais, permissões e vínculos salvos sem expor tokens. Consultas à Meta são limitadas a uma empresa e começam somente quando você as solicita." actions={<ActionButton type="button" variant="secondary" onClick={loadCompanies} disabled={loading} icon={RefreshCw}>Atualizar empresas</ActionButton>} />
    <SectionCard title="Integrações globais da plataforma" description="Este resumo mostra somente presença de configuração e controles de operação. Nenhum segredo é retornado; credenciais Marketing, Instagram e JEV continuam no escopo de cada empresa.">
      {globalStatusLoading ? <LoadingState label="Consultando configurações compartilhadas..." /> : globalStatusError ? <ErrorState title="Estado global indisponível" description="Não foi possível consultar as configurações compartilhadas." action={<ActionButton type="button" onClick={loadGlobalStatus}>Tentar novamente</ActionButton>} /> : globalStatus ? <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <article className="rounded-lg border border-slate-200 bg-white p-3"><p className="text-xs text-slate-500">Aplicativo Meta</p><StatusBadge tone={globalStatus.meta?.public_configuration_ready ? "success" : "warning"}>{globalStatus.meta?.public_configuration_ready ? "Configuração pública presente" : "Configuração pública incompleta"}</StatusBadge><p className="mt-2 text-xs text-slate-600">Configuração de servidor: {globalStatus.meta?.server_configuration_ready ? "presente" : "incompleta"}</p><p className="mt-1 text-xs text-slate-600">Graph API {globalStatus.meta?.graph_api_version || "não informada"} · Marketing {globalStatus.meta?.marketing_graph_api_version || "não informada"}</p></article>
        <article className="rounded-lg border border-slate-200 bg-white p-3"><p className="text-xs text-slate-500">Controles de escrita Meta</p><StatusBadge tone={globalStatus.meta?.marketing_write_enabled ? "warning" : "success"}>{globalStatus.meta?.marketing_write_enabled ? "Escrita Marketing habilitada" : "Escrita Marketing bloqueada"}</StatusBadge><p className="mt-2 text-xs text-slate-600">Ações gerais LIVE: {globalStatus.meta?.live_actions_enabled ? "habilitadas" : "bloqueadas"}</p><p className="mt-1 text-xs text-slate-600">Campanha: {globalStatus.meta?.campaign_create_enabled ? "habilitada" : "bloqueada"} · Anúncio: {globalStatus.meta?.ad_create_enabled ? "habilitado" : "bloqueado"}</p></article>
        <article className="rounded-lg border border-slate-200 bg-white p-3"><p className="text-xs text-slate-500">OpenAI global</p><StatusBadge tone={globalStatus.openai?.global_credential_configured ? "info" : "warning"}>{globalStatus.openai?.global_credential_configured ? "Credencial configurada" : "Sem credencial global"}</StatusBadge><p className="mt-2 text-xs text-slate-600">O seletor mostra apenas o estado; o valor não é exposto.</p></article>
        <article className="rounded-lg border border-slate-200 bg-white p-3"><p className="text-xs text-slate-500">Escopo das integrações</p><p className="mt-1 text-sm text-slate-800">Marketing, Instagram e JEV são configurados por empresa.</p><p className="mt-1 text-xs text-slate-600">A credencial WhatsApp permanece no fluxo do canal correspondente.</p></article>
      </div> : null}
      <div className="mt-4 flex flex-wrap gap-2">
      <Link to="/admin/configuracoes" className="fm-button fm-button-secondary">Abrir configurações da plataforma</Link>
      <Link to="/admin/configuracoes/integracoes/jev" className="fm-button fm-button-secondary ml-2">Configuração JEV por empresa</Link>
      </div>
    </SectionCard>
    <SectionCard title="Estado das integrações por empresa" description="O resumo usa os serviços existentes. A consulta de status Marketing pode verificar a credencial com a Meta; por isso, ela só ocorre após selecionar uma empresa e solicitar a atualização.">
      <label className="fm-admin-field"><span>Buscar empresa</span><div className="relative"><Search size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" /><Input type="search" className="pl-9" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Nome ou identificador" /></div></label>
      {loading ? <LoadingState label="Carregando empresas..." /> : error ? <ErrorState title="Lista indisponível" description="Não foi possível carregar a lista de empresas." action={<ActionButton type="button" onClick={loadCompanies}>Tentar novamente</ActionButton>} /> : visible.length === 0 ? <EmptyState title={companies.length ? "Nenhuma empresa encontrada" : "Nenhuma empresa cadastrada"} description={companies.length ? "Ajuste a busca para encontrar uma empresa." : "A lista não contém empresas cadastradas."} action={companies.length ? <ActionButton type="button" variant="secondary" onClick={() => setSearch("")}>Limpar busca</ActionButton> : null} /> : <ul className="fm-admin-company-picker">
        {visible.map((company) => <li key={company.id}>
          <div><h2>{company.nome_empresa || "Empresa sem nome"}</h2><p className="fm-helper">A conexão será administrada no escopo desta empresa.</p></div>
          <div className="flex flex-wrap gap-2">
            <ActionButton type="button" variant={selectedCompanyId === company.id ? "primary" : "secondary"} aria-pressed={selectedCompanyId === company.id} onClick={() => { setSelectedCompanyId(company.id); setIntegrationStatus(null); setStatusError(""); }}>Ver integrações</ActionButton>
          </div>
        </li>)}
      </ul>}
      {selectedCompany ? <div className="mt-5 space-y-4 rounded-xl border border-slate-200 bg-slate-50 p-4">
        <div className="flex flex-wrap items-center justify-between gap-3"><div><h2 className="font-semibold text-slate-950">{selectedCompany.nome_empresa}</h2><p className="fm-helper">A consulta é somente leitura e não grava configuração.</p></div><ActionButton type="button" onClick={inspectCompany} disabled={statusLoading} icon={RefreshCw}>{statusLoading ? "Consultando..." : "Consultar status"}</ActionButton></div>
        {statusError ? <p role="status" className="rounded-lg border border-amber-200 bg-amber-50 p-3 text-sm text-amber-900">{statusError}</p> : null}
        {statusLoading ? <LoadingState label="Consultando estados dos canais..." /> : null}
        {integrationStatus ? <>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <article className="rounded-lg border border-slate-200 bg-white p-3"><p className="text-xs text-slate-500">Marketing API</p><StatusBadge tone={tokenPresence.tone}>{tokenPresence.label}</StatusBadge></article>
            <article className="rounded-lg border border-slate-200 bg-white p-3"><p className="text-xs text-slate-500">Leitura Meta</p><StatusBadge tone={metaReadiness.tone}>{metaReadiness.label}</StatusBadge><p className="mt-1 text-xs text-slate-600">{integrationStatus.metaCredentials?.ok ? String(metaCredential?.marketing_readiness_status || metaCredential?.status || "Não confirmada") : "Não informada"}</p></article>
            <article className="rounded-lg border border-slate-200 bg-white p-3"><p className="text-xs text-slate-500">Permissão</p><StatusBadge tone={adsRead.tone}>{adsRead.label}</StatusBadge></article>
            <article className="rounded-lg border border-slate-200 bg-white p-3"><p className="text-xs text-slate-500">Instagram</p><StatusBadge tone={integrationStatus.instagram?.ok ? "info" : "warning"}>{integrationStatus.instagram?.ok ? `${(instagramConnections || []).length} conexão(ões) cadastrada(s)` : "Estado não informado"}</StatusBadge></article>
          </div>
          <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
            <article className="rounded-lg border border-slate-200 bg-white p-3"><p className="text-xs text-slate-500">Conta de anúncios</p><StatusBadge tone={integrationStatus.metaConfig?.ok ? presence(Boolean(savedConfig.ad_account_id), "Vínculo configurado", "Não configurado").tone : "warning"}>{integrationStatus.metaConfig?.ok ? presence(Boolean(savedConfig.ad_account_id), "Vínculo configurado", "Não configurado").label : "Não informado"}</StatusBadge></article>
            <article className="rounded-lg border border-slate-200 bg-white p-3"><p className="text-xs text-slate-500">Página</p><StatusBadge tone={integrationStatus.metaConfig?.ok ? presence(Boolean(savedConfig.page_id), "Vínculo configurado", "Não configurado").tone : "warning"}>{integrationStatus.metaConfig?.ok ? presence(Boolean(savedConfig.page_id), "Vínculo configurado", "Não configurado").label : "Não informado"}</StatusBadge></article>
            <article className="rounded-lg border border-slate-200 bg-white p-3"><p className="text-xs text-slate-500">Dataset</p><StatusBadge tone={integrationStatus.metaConfig?.ok ? presence(Boolean(savedConfig.dataset_id), "Vínculo configurado", "Não configurado").tone : "warning"}>{integrationStatus.metaConfig?.ok ? presence(Boolean(savedConfig.dataset_id), "Vínculo configurado", "Não configurado").label : "Não informado"}</StatusBadge></article>
            <article className="rounded-lg border border-slate-200 bg-white p-3"><p className="text-xs text-slate-500">Criação de campanhas</p><StatusBadge tone="warning">Ver readiness na área de campanhas</StatusBadge></article>
          </div>
          <p className="text-xs text-slate-500">Vínculo configurado não comprova acesso ao ativo. Leitura validada não comprova permissão de escrita nem readiness para criação de campanhas.{statusUpdatedAt ? ` Consultado às ${statusUpdatedAt.toLocaleTimeString("pt-BR")}.` : ""}</p>
          <div className="flex flex-wrap gap-2">
            <Link className="fm-button fm-button-secondary" to={`/admin/empresas/${selectedCompany.id}?area=canais`}><MessageCircle size={15} aria-hidden="true" />Administrar canais e credenciais</Link>
            <Link className="fm-button fm-button-secondary" to={`/admin/empresas/${selectedCompany.id}/meta`}><ShoppingBag size={15} aria-hidden="true" />Campanhas e Meta</Link>
            <Link className="fm-button fm-button-secondary" to={`/admin/empresas/${selectedCompany.id}/instagram`}><Instagram size={15} aria-hidden="true" />Detalhes do Instagram</Link>
          </div>
        </> : <p className="text-sm text-slate-600">Selecione “Ver integrações” e depois “Consultar status” para carregar o estado desta empresa.</p>}
      </div> : null}
    </SectionCard>
  </div>;
}

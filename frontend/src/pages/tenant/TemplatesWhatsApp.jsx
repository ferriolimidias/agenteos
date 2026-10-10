import { useCallback, useEffect, useMemo, useState } from "react";
import { Eye, Filter, Loader2, RefreshCw, Search, ShieldCheck, SlidersHorizontal } from "lucide-react";

import MetaTemplateSelector from "../../components/MetaTemplateSelector";
import api from "../../services/api";
import { getActiveEmpresaId } from "../../utils/auth";

const statusLabels = {
  approved: "Aprovado",
  pending: "Pendente",
  rejected: "Rejeitado",
  paused: "Pausado",
  disabled: "Desativado",
  archived: "Arquivado",
  deleted: "Excluído",
  unknown: "Estado não reconhecido",
};

const categoryLabels = {
  marketing: "Marketing",
  utility: "Utilidade",
  authentication: "Autenticação",
  unknown: "Categoria não reconhecida",
};

function badgeClass(value) {
  if (value === "approved") return "border-emerald-200 bg-emerald-50 text-emerald-700";
  if (value === "pending") return "border-amber-200 bg-amber-50 text-amber-700";
  if (["rejected", "disabled", "deleted"].includes(value)) return "border-red-200 bg-red-50 text-red-700";
  return "border-slate-200 bg-slate-50 text-slate-600";
}

function formatDate(value) {
  if (!value) return "-";
  try {
    return new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(new Date(value));
  } catch {
    return "-";
  }
}

function ComponentPreview({ preview }) {
  const components = preview?.components || [];
  if (!components.length) {
    return <div className="rounded border border-slate-200 bg-slate-50 p-4 text-sm text-slate-500">Nenhum componente sincronizado para preview.</div>;
  }
  return (
    <div className="space-y-3">
      {components.map((component, index) => (
        <div key={`${component.type}-${index}`} className="rounded border border-slate-200 bg-white p-3">
          <div className="mb-2 flex items-center justify-between gap-3">
            <span className="text-xs font-semibold uppercase text-slate-500">{component.type}</span>
            {component.format ? <span className="rounded border border-slate-200 px-2 py-0.5 text-xs text-slate-500">{component.format}</span> : null}
          </div>
          {component.text ? <p className="whitespace-pre-wrap text-sm text-slate-800">{component.text}</p> : null}
          {component.buttons?.length ? (
            <div className="mt-3 flex flex-wrap gap-2">
              {component.buttons.map((button, buttonIndex) => (
                <span key={buttonIndex} className="rounded border border-slate-200 bg-slate-50 px-2 py-1 text-xs text-slate-600">
                  {button.type || "Botão"}
                </span>
              ))}
            </div>
          ) : null}
        </div>
      ))}
    </div>
  );
}

export default function TemplatesWhatsApp() {
  const empresaId = getActiveEmpresaId();
  const [connections, setConnections] = useState([]);
  const [selectedConnection, setSelectedConnection] = useState("");
  const [templates, setTemplates] = useState([]);
  const [selectedTemplate, setSelectedTemplate] = useState(null);
  const [selectedTemplateId, setSelectedTemplateId] = useState("");
  const [latestSync, setLatestSync] = useState(null);
  const [filters, setFilters] = useState({ search: "", status: "", category: "", language: "" });
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [syncing, setSyncing] = useState(false);
  const [message, setMessage] = useState("");

  const officialConnections = useMemo(
    () =>
      connections.filter((connection) => {
        const provider = String(connection.provider || connection.tipo || "").toLowerCase();
        return provider.includes("meta");
      }),
    [connections]
  );
  const activeConnection = officialConnections.find((connection) => String(connection.id) === String(selectedConnection));

  const loadConnections = useCallback(async () => {
    if (!empresaId) return;
    const res = await api.get(`/empresas/${empresaId}/conexoes/`);
    const list = Array.isArray(res.data) ? res.data : [];
    setConnections(list);
    const firstOfficial = list.find((connection) => String(connection.provider || connection.tipo || "").toLowerCase().includes("meta"));
    if (firstOfficial) setSelectedConnection((current) => current || firstOfficial.id);
  }, [empresaId]);

  const loadTemplates = useCallback(async () => {
    if (!empresaId) return;
    setLoading(true);
    try {
      const params = {};
      if (selectedConnection) params.conexao_id = selectedConnection;
      if (filters.search) params.search = filters.search;
      if (filters.status) params.status = filters.status;
      if (filters.category) params.category = filters.category;
      if (filters.language) params.language = filters.language;
      const [templatesRes, syncRes] = await Promise.all([
        api.get(`/empresas/${empresaId}/meta/templates`, { params }),
        api.get(`/empresas/${empresaId}/meta/templates/sync/latest`, { params: selectedConnection ? { conexao_id: selectedConnection } : {} }),
      ]);
      setTemplates(Array.isArray(templatesRes.data) ? templatesRes.data : []);
      setLatestSync(syncRes.data?.last_sync || null);
    } catch (error) {
      setMessage(error?.response?.data?.detail || "Não foi possível carregar os templates.");
    } finally {
      setLoading(false);
    }
  }, [empresaId, selectedConnection, filters]);

  useEffect(() => {
    loadConnections().catch(() => setMessage("Não foi possível carregar conexões."));
  }, [loadConnections]);

  useEffect(() => {
    loadTemplates();
  }, [loadTemplates]);

  const openDetails = async (template) => {
    if (!empresaId || !selectedConnection) return;
    setDetailLoading(true);
    try {
      const res = await api.get(`/empresas/${empresaId}/meta/templates/${template.id}`, { params: { conexao_id: selectedConnection } });
      setSelectedTemplate(res.data);
      setSelectedTemplateId(template.id);
      setMessage("");
    } catch (error) {
      setMessage(error?.response?.data?.detail || "Não foi possível abrir o template.");
    } finally {
      setDetailLoading(false);
    }
  };

  const selectTemplate = (templateId) => {
    setSelectedTemplateId(templateId);
    const template = templates.find((item) => String(item.id) === String(templateId));
    if (template) {
      openDetails(template);
    } else {
      setSelectedTemplate(null);
    }
  };

  const requestSync = async () => {
    if (!empresaId || !selectedConnection) return;
    setSyncing(true);
    try {
      await api.post(`/empresas/${empresaId}/meta/templates/sync`, null, { params: { conexao_id: selectedConnection } });
      setMessage("Sincronização solicitada.");
    } catch (error) {
      setMessage(error?.response?.data?.detail || "Sincronização real ainda não está disponível.");
    } finally {
      setSyncing(false);
    }
  };

  if (!empresaId) {
    return <div className="p-6 text-sm text-slate-600">Empresa não identificada.</div>;
  }

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-4 border-b border-slate-200 pb-5 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-950">Templates do WhatsApp</h1>
          <p className="mt-1 text-sm text-slate-500">Modelos oficiais aprovados pela Meta para uso futuro em campanhas e follow-ups.</p>
        </div>
        <button
          type="button"
          onClick={requestSync}
          disabled={!selectedConnection || syncing}
          className="inline-flex items-center justify-center gap-2 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-semibold text-slate-700 hover:bg-slate-50 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {syncing ? <Loader2 className="h-4 w-4 animate-spin" /> : <RefreshCw className="h-4 w-4" />}
          Sincronizar
        </button>
      </div>

      {message ? <div className="rounded border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">{message}</div> : null}

      <section className="grid gap-4 md:grid-cols-4">
        <div className="rounded-md border border-slate-200 bg-white p-4">
          <p className="text-xs font-semibold uppercase text-slate-500">Total</p>
          <p className="mt-2 text-2xl font-semibold text-slate-950">{templates.length}</p>
        </div>
        <div className="rounded-md border border-slate-200 bg-white p-4">
          <p className="text-xs font-semibold uppercase text-slate-500">Aprovados</p>
          <p className="mt-2 text-2xl font-semibold text-emerald-700">{templates.filter((item) => item.normalized_status === "approved").length}</p>
        </div>
        <div className="rounded-md border border-slate-200 bg-white p-4">
          <p className="text-xs font-semibold uppercase text-slate-500">WABA</p>
          <p className="mt-2 truncate text-sm font-semibold text-slate-950">{activeConnection?.waba_id || "Não associado"}</p>
        </div>
        <div className="rounded-md border border-slate-200 bg-white p-4">
          <p className="text-xs font-semibold uppercase text-slate-500">Última sincronização</p>
          <p className="mt-2 text-sm font-semibold text-slate-950">{formatDate(latestSync?.completed_at || latestSync?.started_at)}</p>
        </div>
      </section>

      <section className="grid gap-4 lg:grid-cols-[320px_1fr]">
        <aside className="space-y-4">
          <div className="rounded-md border border-slate-200 bg-white p-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-800">
              <SlidersHorizontal className="h-4 w-4" /> Conexão oficial
            </div>
            <select
              value={selectedConnection}
              onChange={(event) => setSelectedConnection(event.target.value)}
              className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm outline-none focus:border-slate-500"
            >
              <option value="">Selecione</option>
              {officialConnections.map((connection) => (
                <option key={connection.id} value={connection.id}>
                  {connection.nome_instancia || "WhatsApp Oficial"}
                </option>
              ))}
            </select>
            {!officialConnections.length ? <p className="mt-3 text-sm text-slate-500">Nenhuma conexão oficial encontrada.</p> : null}
          </div>

          <div className="rounded-md border border-slate-200 bg-white p-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-800">
              <Filter className="h-4 w-4" /> Filtros
            </div>
            <div className="space-y-3">
              <label className="relative block">
                <Search className="pointer-events-none absolute left-3 top-2.5 h-4 w-4 text-slate-400" />
                <input
                  value={filters.search}
                  onChange={(event) => setFilters((prev) => ({ ...prev, search: event.target.value }))}
                  placeholder="Buscar por nome"
                  className="w-full rounded-md border border-slate-300 py-2 pl-9 pr-3 text-sm outline-none focus:border-slate-500"
                />
              </label>
              <select value={filters.status} onChange={(event) => setFilters((prev) => ({ ...prev, status: event.target.value }))} className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm">
                <option value="">Todos os estados</option>
                {Object.entries(statusLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
              <select value={filters.category} onChange={(event) => setFilters((prev) => ({ ...prev, category: event.target.value }))} className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm">
                <option value="">Todas as categorias</option>
                {Object.entries(categoryLabels).map(([value, label]) => <option key={value} value={value}>{label}</option>)}
              </select>
              <input value={filters.language} onChange={(event) => setFilters((prev) => ({ ...prev, language: event.target.value }))} placeholder="Idioma, ex: pt_BR" className="w-full rounded-md border border-slate-300 px-3 py-2 text-sm" />
            </div>
          </div>

          <div className="rounded-md border border-slate-200 bg-white p-4">
            <MetaTemplateSelector
              templates={templates}
              value={selectedTemplateId}
              onChange={selectTemplate}
              disabled={!templates.length}
            />
          </div>
        </aside>

        <div className="overflow-hidden rounded-md border border-slate-200 bg-white">
          {loading ? (
            <div className="flex min-h-[280px] items-center justify-center text-sm text-slate-500"><Loader2 className="mr-2 h-4 w-4 animate-spin" />Carregando templates</div>
          ) : templates.length === 0 ? (
            <div className="flex min-h-[280px] flex-col items-center justify-center px-6 text-center">
              <ShieldCheck className="mb-3 h-8 w-8 text-slate-400" />
              <p className="text-sm font-semibold text-slate-900">Nenhum template sincronizado</p>
              <p className="mt-1 max-w-md text-sm text-slate-500">Conecte uma conta oficial e use a sincronização quando ela estiver liberada para trazer os modelos aprovados.</p>
            </div>
          ) : (
            <table className="min-w-full divide-y divide-slate-200 text-sm">
              <thead className="bg-slate-50 text-left text-xs font-semibold uppercase text-slate-500">
                <tr>
                  <th className="px-4 py-3">Nome</th>
                  <th className="px-4 py-3">Idioma</th>
                  <th className="px-4 py-3">Categoria</th>
                  <th className="px-4 py-3">Estado</th>
                  <th className="px-4 py-3">Qualidade</th>
                  <th className="px-4 py-3">Última sync</th>
                  <th className="px-4 py-3"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-slate-100">
                {templates.map((template) => (
                  <tr key={template.id} className="hover:bg-slate-50">
                    <td className="px-4 py-3 font-medium text-slate-900">{template.name}</td>
                    <td className="px-4 py-3 text-slate-600">{template.language}</td>
                    <td className="px-4 py-3 text-slate-600">{categoryLabels[template.normalized_category] || "Categoria não reconhecida"}</td>
                    <td className="px-4 py-3"><span className={`rounded-full border px-2 py-1 text-xs font-semibold ${badgeClass(template.normalized_status)}`}>{statusLabels[template.normalized_status] || "Estado não reconhecido"}</span></td>
                    <td className="px-4 py-3 text-slate-600">{template.quality_rating || "-"}</td>
                    <td className="px-4 py-3 text-slate-600">{formatDate(template.last_synced_at)}</td>
                    <td className="px-4 py-3 text-right">
                      <button onClick={() => openDetails(template)} className="inline-flex items-center gap-2 rounded-md border border-slate-300 px-3 py-1.5 text-xs font-semibold text-slate-700 hover:bg-slate-50">
                        <Eye className="h-3.5 w-3.5" /> Detalhes
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </section>

      {selectedTemplate ? (
        <section className="rounded-md border border-slate-200 bg-white p-5">
          <div className="mb-4 flex items-start justify-between gap-4">
            <div>
              <h2 className="text-lg font-semibold text-slate-950">{selectedTemplate.name}</h2>
              <p className="mt-1 text-sm text-slate-500">{selectedTemplate.language} · {categoryLabels[selectedTemplate.normalized_category] || "Categoria não reconhecida"}</p>
            </div>
            {detailLoading ? <Loader2 className="h-5 w-5 animate-spin text-slate-400" /> : null}
          </div>
          <ComponentPreview preview={selectedTemplate.preview} />
        </section>
      ) : null}
    </div>
  );
}

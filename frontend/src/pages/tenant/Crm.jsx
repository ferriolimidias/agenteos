import { useEffect, useState } from "react";
import api from "../../services/api";
import { Users, Plus, Phone, Clock, MessageSquare, Upload, X, Trash2 } from "lucide-react";
import { getActiveEmpresaId, getStoredUser } from "../../utils/auth";
import { normalizeLeadTags } from "../../utils/leadTags";
import LeadTagsEditor from "../../components/LeadTagsEditor";
import LeadDetailsModal from "../../components/LeadDetailsModal";

function formatDate(value) {
  if (!value) return "Hoje";
  return new Date(value).toLocaleDateString("pt-BR");
}

function formatCurrency(value) {
  return new Intl.NumberFormat("pt-BR", {
    style: "currency",
    currency: "BRL",
  }).format(value || 0);
}

export default function Crm() {
  const [funil, setFunil] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selectedLead, setSelectedLead] = useState(null);
  const [availableTags, setAvailableTags] = useState([]);
  const [showImportModal, setShowImportModal] = useState(false);
  const [importFile, setImportFile] = useState(null);
  const [importPreviewColumns, setImportPreviewColumns] = useState([]);
  const [importSelectedTags, setImportSelectedTags] = useState([]);
  const [importSaving, setImportSaving] = useState(false);
  const [importInfo, setImportInfo] = useState("");
  const [toastMessage, setToastMessage] = useState("");

  const user = getStoredUser();
  const empresaId = getActiveEmpresaId();

  const fetchCrmData = async () => {
    try {
      setLoading(true);
      const res = await api.get(`/empresas/${empresaId}/crm`);
      setFunil(res.data);
    } catch (err) {
      console.error("Erro ao buscar dados do CRM:", err);
    } finally {
      setLoading(false);
    }
  };

  const fetchOfficialTags = async () => {
    try {
      const res = await api.get(`/empresas/${empresaId}/crm/tags/oficiais`);
      setAvailableTags(res.data || []);
    } catch (err) {
      console.error("Erro ao carregar tags oficiais:", err);
      setAvailableTags([]);
    }
  };

  useEffect(() => {
    if (empresaId) {
      fetchCrmData();
      fetchOfficialTags();
    }
  }, [empresaId]);

  const updateLeadInState = (leadId, updates) => {
    setFunil((prev) => {
      if (!prev?.etapas) return prev;
      return {
        ...prev,
        etapas: prev.etapas.map((etapa) => ({
          ...etapa,
          leads: (etapa.leads || []).map((lead) =>
            lead.id === leadId ? { ...lead, ...updates } : lead
          ),
        })),
      };
    });
    setSelectedLead((prev) => (prev?.id === leadId ? { ...prev, ...updates } : prev));
  };

  const findLeadById = (leadId) => {
    for (const etapa of funil?.etapas || []) {
      const lead = (etapa?.leads || []).find((item) => item?.id === leadId);
      if (lead) return lead;
    }
    return selectedLead?.id === leadId ? selectedLead : null;
  };

  const normalizeSearchText = (value) =>
    String(value || "")
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, "")
      .toLowerCase();

  const isConversionTag = (tag, allTags) => {
    // 1. Tenta encontrar o objeto da tag pelo ID ou pelo Próprio Objeto
    const tagObj = typeof tag === "object" ? tag : (allTags || []).find((t) => t.id === tag);

    if (!tagObj) return false;

    // 2. Normaliza o nome para ignorar acentos e maiúsculas
    const nome = (tagObj.nome || "")
      .toLowerCase()
      .normalize("NFD")
      .replace(/[\u0300-\u036f]/g, ""); // Remove acentos (ex: conversão -> conversao)

    // 3. Verifica se tem a flag do banco OU as palavras-chave
    return (
      tagObj.disparar_conversao_ads === true ||
      nome.includes("conversao") ||
      nome.includes("venda") ||
      nome.includes("pago")
    );
  };

  const showToast = (message) => {
    setToastMessage(message);
    window.setTimeout(() => setToastMessage(""), 2800);
  };

  const handleConversionTrigger = async (leadId, previousTags, nextTags) => {
    // Extrai o ID ou Nome de cada tag para comparação, seja objeto ou string
    const getTagValue = (t) => {
      if (!t) return "";
      if (typeof t === "object") return String(t.id || t.nome || "").toLowerCase();
      return String(t).toLowerCase();
    };

    const prevValues = (previousTags || []).map(getTagValue).filter(Boolean);
    const prevSet = new Set(prevValues);

    // Filtra apenas o que realmente entrou de novo
    const novasTags = (nextTags || []).filter((tag) => {
      const val = getTagValue(tag);
      return val && !prevSet.has(val);
    });
    const novaTagConversao = novasTags.find((tagId) => isConversionTag(tagId, availableTags));
    if (!novaTagConversao) return;

    const valorInput = window.prompt("Qual o valor desta conversão?");
    if (valorInput === null) return;
    const valorNormalizado = Number(String(valorInput).replace(",", ".").trim());
    if (!Number.isFinite(valorNormalizado) || valorNormalizado < 0) {
      alert("Valor inválido para conversão.");
      return;
    }

    const lead = findLeadById(leadId);
    const telefoneLead = String(lead?.telefone_contato || lead?.telefone || "").trim() || null;

    try {
      await api.post(`/empresas/${empresaId}/teste-meta-capi`, {
        valor: valorNormalizado,
        moeda: "BRL",
        telefone: telefoneLead,
        test_event_code: null,
      });
      showToast("Conversão enviada para a Meta!");
    } catch (err) {
      console.error("Erro ao disparar conversão para Meta:", err);
      alert("Não foi possível enviar a conversão para a Meta.");
    }
  };

  const handleLeadTagsChange = async (leadId, nextTags) => {
    const leadAtual = findLeadById(leadId);
    const tagsAnteriores = normalizeLeadTags(leadAtual?.tags);
    await api.put(`/empresas/${empresaId}/crm/leads/${leadId}`, { tags: nextTags });
    updateLeadInState(leadId, { tags: nextTags });
    await handleConversionTrigger(leadId, tagsAnteriores, nextTags);
  };

  const handleLeadSave = async (leadId, updates) => {
    const res = await api.put(`/empresas/${empresaId}/crm/leads/${leadId}`, updates);
    updateLeadInState(leadId, {
      ...updates,
      tags: res.data?.tags || updates.tags,
      valor_conversao: res.data?.valor_conversao ?? updates.valor_conversao,
    });
  };

  const handleDeleteLead = async (leadId) => {
    if (!leadId) return;
    const confirmou = window.confirm("Tem certeza que deseja excluir este lead?");
    if (!confirmou) return;

    try {
      await api.delete(`/empresas/${empresaId}/crm/leads/${leadId}`);
      setFunil((prev) => {
        if (!prev?.etapas) return prev;
        return {
          ...prev,
          etapas: prev.etapas.map((etapa) => ({
            ...etapa,
            leads: (etapa.leads || []).filter((lead) => lead.id !== leadId),
          })),
        };
      });
      setSelectedLead((prev) => (prev?.id === leadId ? null : prev));
    } catch (err) {
      console.error("Erro ao excluir lead:", err);
      alert(err.response?.data?.detail || "Não foi possível excluir o lead.");
    }
  };

  const resetImportModal = () => {
    setShowImportModal(false);
    setImportFile(null);
    setImportPreviewColumns([]);
    setImportSelectedTags([]);
    setImportInfo("");
  };

  const handleImportFileChange = async (event) => {
    const file = event.target.files?.[0];
    setImportFile(file || null);
    setImportInfo("");
    if (!file) {
      setImportPreviewColumns([]);
      return;
    }

    if (String(file.name || "").toLowerCase().endsWith(".xlsx")) {
      setImportPreviewColumns([]);
      setImportInfo("Arquivo Excel selecionado. O preview de colunas será detectado no backend durante a importação.");
      return;
    }

    try {
      const text = await file.text();
      const firstLine = text.split(/\r?\n/).find((line) => line.trim()) || "";
      const separator = firstLine.includes(";") ? ";" : ",";
      const columns = firstLine.split(separator).map((item) => item.trim()).filter(Boolean);
      setImportPreviewColumns(columns);
    } catch (err) {
      console.error("Erro ao ler preview do arquivo:", err);
      setImportPreviewColumns([]);
      setImportInfo("Não foi possível ler o preview do arquivo no navegador.");
    }
  };

  const handleSubmitImport = async (e) => {
    e.preventDefault();
    if (!importFile) return;
    try {
      setImportSaving(true);
      const formData = new FormData();
      formData.append("file", importFile);
      formData.append("tags_iniciais", JSON.stringify(importSelectedTags));
      const res = await api.post(`/empresas/${empresaId}/leads/importar`, formData, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      resetImportModal();
      await fetchCrmData();
      setImportInfo(
        `Importação concluída: ${res.data?.inseridos || 0} inseridos, ${res.data?.atualizados || 0} atualizados.`
      );
    } catch (err) {
      console.error("Erro ao importar leads:", err);
      alert(err.response?.data?.detail || "Não foi possível importar os leads.");
    } finally {
      setImportSaving(false);
    }
  };

  if (!user) return <div className="p-8 text-center text-gray-500">Faça login novamente.</div>;

  return (
    <div className="flex h-full flex-col space-y-6">
      {toastMessage ? (
        <div className="fixed right-4 top-4 z-[70] rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm font-medium text-emerald-700 shadow-lg">
          {toastMessage}
        </div>
      ) : null}

      <div className="flex flex-shrink-0 items-start justify-between gap-4">
        <div>
          <h1 className="flex items-center gap-3 text-3xl font-bold tracking-tight text-gray-900">
            <Users className="text-blue-600" size={32} />
            CRM Dinâmico
          </h1>
          <p className="mt-1 text-gray-500">
            {funil ? `Funil ativo: ${funil.funil_nome}` : "Gerencie seus leads capturados pela IA."}
          </p>
        </div>

        <div className="flex items-center gap-3">
          <button
            onClick={() => setShowImportModal(true)}
            className="flex items-center space-x-2 rounded-lg border border-blue-200 bg-white px-4 py-2.5 font-medium text-blue-700 shadow-sm transition-colors hover:bg-blue-50"
          >
            <Upload size={18} />
            <span>Importar Leads</span>
          </button>
          <button
            onClick={() => alert("Criação manual de Lead em breve! A IA já faz isso automaticamente.")}
            className="flex items-center space-x-2 rounded-lg bg-blue-600 px-4 py-2.5 font-medium text-white shadow-sm transition-colors hover:bg-blue-700"
          >
            <Plus size={20} />
            <span>Novo Lead</span>
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex h-64 flex-1 items-center justify-center">
          <div className="flex animate-pulse flex-col items-center">
            <Users className="mb-2 text-gray-300" size={48} />
            <span className="font-medium text-gray-400">Carregando funil...</span>
          </div>
        </div>
      ) : !funil || !funil.etapas ? (
        <div className="flex flex-1 items-center justify-center rounded-2xl border border-gray-100 bg-white p-12">
          <p className="text-gray-500">Nenhum dado de CRM encontrado.</p>
        </div>
      ) : (
        <div className="custom-scrollbar flex-1 overflow-x-auto pb-4">
          <div className="flex h-full min-w-max space-x-6">
            {funil.etapas.map((etapa) => {
              const totalVendasEtapa = (etapa.leads || []).reduce(
                (subtotal, lead) => subtotal + Number(lead.valor_conversao || 0),
                0
              );
              const totalLeadsConvertidosEtapa = (etapa.leads || []).filter(
                (lead) => Number(lead.valor_conversao || 0) > 0
              ).length;
              const resumoFaturamentoEtapa =
                totalLeadsConvertidosEtapa > 0
                  ? `${totalLeadsConvertidosEtapa} vendas • ${formatCurrency(totalVendasEtapa)}`
                  : formatCurrency(totalVendasEtapa);

              return (
                <div
                  key={etapa.id}
                  className="flex w-80 flex-col overflow-hidden rounded-2xl border border-gray-200/60 bg-gray-50/80 shadow-sm"
                >
                  <div className="flex items-center justify-between border-b border-gray-200/60 bg-gray-100/50 px-4 py-3">
                    <div className="min-w-0">
                      <h3 className="truncate font-semibold text-gray-800">{etapa.nome}</h3>
                      <p
                        className={`mt-1 text-xs font-medium ${
                          totalLeadsConvertidosEtapa > 0 ? "text-emerald-700" : "text-gray-400"
                        }`}
                      >
                        {resumoFaturamentoEtapa}
                      </p>
                    </div>
                    <span className="rounded-md border border-gray-200 bg-white px-2 py-1 text-xs font-bold text-gray-600 shadow-sm">
                      {etapa.leads?.length || 0}
                    </span>
                  </div>

                  <div className="custom-scrollbar min-h-[150px] flex-1 space-y-3 overflow-y-auto p-3">
                    {!etapa.leads || etapa.leads.length === 0 ? (
                      <div className="flex h-full items-center justify-center rounded-xl border-2 border-dashed border-gray-200 p-4 text-center text-sm font-medium text-gray-400/70">
                        Nenhum lead nesta etapa
                      </div>
                    ) : (
                      etapa.leads.map((lead) => (
                        <div
                          key={lead.id}
                          onClick={() => setSelectedLead(lead)}
                          className="group cursor-pointer rounded-xl border border-gray-100 bg-white p-4 shadow-[0_1px_3px_rgba(0,0,0,0.05)] transition-all hover:border-blue-300 hover:shadow-md"
                        >
                          <div className="mb-2 flex justify-between gap-3">
                            <h4 className="line-clamp-1 font-semibold text-gray-900">{lead.nome_contato}</h4>
                            <div className="flex items-center gap-1">
                              <button
                                className="rounded-lg bg-blue-50 p-1.5 text-blue-600 opacity-0 transition-opacity group-hover:opacity-100"
                                onClick={(e) => e.stopPropagation()}
                              >
                                <MessageSquare size={14} />
                              </button>
                              <button
                                className="rounded-lg bg-red-50 p-1.5 text-red-600 opacity-0 transition-opacity hover:bg-red-100 group-hover:opacity-100"
                                onClick={(e) => {
                                  e.stopPropagation();
                                  handleDeleteLead(lead.id);
                                }}
                                title="Excluir lead"
                              >
                                <Trash2 size={14} />
                              </button>
                            </div>
                          </div>

                          {lead.telefone ? (
                            <div className="mb-3 flex items-center gap-2 text-sm text-gray-500">
                              <Phone size={14} className="text-green-600" />
                              <span className="font-medium text-gray-700">{lead.telefone}</span>
                            </div>
                          ) : null}

                          {lead.historico_resumo ? (
                            <p className="mb-3 line-clamp-2 rounded-lg border border-gray-100 bg-gray-50 p-2 text-xs text-gray-500">
                              {lead.historico_resumo}
                            </p>
                          ) : null}

                          <LeadTagsEditor
                            tags={normalizeLeadTags(lead?.tags)}
                            compact
                            placeholder="Selecione tags oficiais"
                            onChange={(nextTags) => handleLeadTagsChange(lead.id, nextTags)}
                            tagDefinitions={availableTags}
                          />

                          <div className="mt-3 flex items-center justify-between gap-3">
                            <span className="text-[11px] font-semibold uppercase tracking-wide text-gray-400">
                              Valor Venda
                            </span>
                            <span
                              className={`rounded-full px-2.5 py-1 text-xs font-semibold ${
                                Number(lead.valor_conversao || 0) > 0
                                  ? "bg-emerald-100 text-emerald-700"
                                  : "bg-gray-100 text-gray-600"
                              }`}
                            >
                              {formatCurrency(lead.valor_conversao || 0)}
                            </span>
                          </div>

                          <div className="mt-3 flex items-center justify-between border-t border-gray-50 pt-3">
                            <div className="flex items-center gap-1 text-[11px] font-medium text-gray-400 opacity-80">
                              <Clock size={12} />
                              {formatDate(lead.criado_em)}
                            </div>
                            <span className="text-[11px] font-medium text-blue-600">Detalhes</span>
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              );
            })}

            <div className="flex min-h-[500px] w-80 flex-col items-center justify-center rounded-2xl border-2 border-dashed border-gray-200 opacity-50 transition-all hover:border-blue-300 hover:bg-gray-50 hover:opacity-100">
              <div className="flex items-center gap-2 pb-20 font-medium text-gray-500">
                <Plus size={20} />
                Nova Etapa
              </div>
            </div>
          </div>
        </div>
      )}

      <LeadDetailsModal
        open={Boolean(selectedLead)}
        lead={selectedLead}
        onClose={() => setSelectedLead(null)}
        onSaveLead={handleLeadSave}
        onSaveTags={handleLeadTagsChange}
        availableTags={availableTags}
      />

      {showImportModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-lg bg-white shadow-xl">
            <div className="flex shrink-0 items-center justify-between border-b border-gray-100 p-4 md:p-6">
              <div>
                <h2 className="text-xl font-bold text-gray-900">Importar Leads</h2>
                <p className="text-sm text-gray-500">Envie um CSV ou Excel com colunas de Nome e Telefone.</p>
              </div>
              <button type="button" onClick={resetImportModal} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100 hover:text-gray-700">
                <X size={18} />
              </button>
            </div>

            <form onSubmit={handleSubmitImport} className="flex flex-1 flex-col overflow-hidden">
              <div className="flex-1 overflow-y-auto p-4 md:p-6">
                <div className="space-y-5">
                  <div>
                    <label className="mb-1 block text-sm font-medium text-gray-700">Arquivo</label>
                    <input
                      type="file"
                      accept=".csv,.xlsx"
                      onChange={handleImportFileChange}
                      className="w-full rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm text-gray-800"
                    />
                  </div>

                  <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Preview das colunas</p>
                    {importPreviewColumns.length > 0 ? (
                      <div className="flex flex-wrap gap-2">
                        {importPreviewColumns.map((column) => (
                          <span key={column} className="rounded-full border border-blue-200 bg-blue-50 px-3 py-1 text-xs font-medium text-blue-700">
                            {column}
                          </span>
                        ))}
                      </div>
                    ) : (
                      <p className="text-sm text-gray-500">
                        {importInfo || "Selecione um arquivo para visualizar ou detectar as colunas."}
                      </p>
                    )}
                  </div>

                  <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                    <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Tags iniciais da lista</p>
                    <div className="flex flex-wrap gap-2">
                      {availableTags.map((tag) => {
                        const selected = importSelectedTags.includes(tag.nome);
                        return (
                          <button
                            key={tag.id}
                            type="button"
                            onClick={() =>
                              setImportSelectedTags((prev) =>
                                selected ? prev.filter((item) => item !== tag.nome) : [...prev, tag.nome]
                              )
                            }
                            className={`rounded-full border px-3 py-1 text-xs font-semibold transition-colors ${
                              selected
                                ? "border-blue-300 bg-blue-600 text-white"
                                : "border-gray-200 bg-white text-gray-700 hover:bg-gray-100"
                            }`}
                          >
                            {tag.nome}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </div>
              </div>
              <div className="flex shrink-0 justify-end gap-3 rounded-b-lg border-t border-gray-100 bg-gray-50 p-4 md:p-6">
                <button type="button" onClick={resetImportModal} className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm font-medium text-gray-600 hover:bg-gray-50">
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={!importFile || importSaving}
                  className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {importSaving ? "Importando..." : "Importar leads"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </div>
  );
}

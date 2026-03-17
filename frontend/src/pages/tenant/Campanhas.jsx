import { useEffect, useMemo, useRef, useState } from "react";
import {
  BellRing,
  FileText,
  Megaphone,
  Pencil,
  Plus,
  RefreshCw,
  Send,
  Trash2,
  X,
} from "lucide-react";

import api from "../../services/api";
import { getActiveEmpresaId } from "../../utils/auth";

const initialTemplateForm = {
  nome: "",
  texto_template: "",
};

const initialCampanhaForm = {
  nome: "",
  template_id: "",
  tag: "",
};

const suggestedTemplateVariables = ["nome", "telefone", "historico_resumo", "cidade", "produto_interesse"];

function formatEstimatedDuration(totalSeconds) {
  if (!totalSeconds || totalSeconds <= 0) return "~ 0 min";

  const roundedSeconds = Math.round(totalSeconds);
  const hours = Math.floor(roundedSeconds / 3600);
  const minutes = Math.ceil((roundedSeconds % 3600) / 60);

  if (hours <= 0) {
    return `~ ${Math.max(1, minutes)} min`;
  }

  if (minutes === 0) {
    return `~ ${hours}h`;
  }

  return `~ ${hours}h ${minutes}min`;
}

function formatDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString("pt-BR");
}

function StatusBadge({ status }) {
  const normalized = String(status || "").toLowerCase();
  const classes =
    normalized === "concluido"
      ? "bg-emerald-50 text-emerald-700 border-emerald-200"
      : normalized === "erro"
        ? "bg-red-50 text-red-700 border-red-200"
        : normalized === "executando"
          ? "bg-amber-50 text-amber-700 border-amber-200"
          : "bg-blue-50 text-blue-700 border-blue-200";

  return (
    <span className={`inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold ${classes}`}>
      {status}
    </span>
  );
}

export default function Campanhas() {
  const empresaId = getActiveEmpresaId();
  const templateTextareaRef = useRef(null);

  const [activeTab, setActiveTab] = useState("templates");
  const [templates, setTemplates] = useState([]);
  const [campanhas, setCampanhas] = useState([]);
  const [tagsDisponiveis, setTagsDisponiveis] = useState([]);
  const [empresaConfig, setEmpresaConfig] = useState({
    disparo_delay_min: 3,
    disparo_delay_max: 7,
  });
  const [loadingTemplates, setLoadingTemplates] = useState(true);
  const [loadingCampanhas, setLoadingCampanhas] = useState(true);
  const [showTemplateModal, setShowTemplateModal] = useState(false);
  const [showCampanhaModal, setShowCampanhaModal] = useState(false);
  const [editingTemplate, setEditingTemplate] = useState(null);
  const [templateForm, setTemplateForm] = useState(initialTemplateForm);
  const [campanhaForm, setCampanhaForm] = useState(initialCampanhaForm);
  const [savingTemplate, setSavingTemplate] = useState(false);
  const [savingCampanha, setSavingCampanha] = useState(false);
  const [deletingTemplateId, setDeletingTemplateId] = useState(null);
  const [previewData, setPreviewData] = useState(null);
  const [loadingPreview, setLoadingPreview] = useState(false);
  const [previewError, setPreviewError] = useState("");
  const [toast, setToast] = useState(null);

  const templatesEmpty = useMemo(() => !templates || templates.length === 0, [templates]);
  const campanhasEmpty = useMemo(() => !campanhas || campanhas.length === 0, [campanhas]);
  const totalLeadsPreview = previewData?.total_leads || 0;
  const delayMin = Number(empresaConfig?.disparo_delay_min ?? 3);
  const delayMax = Number(empresaConfig?.disparo_delay_max ?? 7);
  const tempoMedioDisparo = (delayMin + delayMax) / 2;
  const estimativaEnvio = formatEstimatedDuration(totalLeadsPreview * tempoMedioDisparo);
  const campanhaPodeEnviar =
    Boolean(campanhaForm.nome.trim() && campanhaForm.template_id && campanhaForm.tag) &&
    !loadingPreview &&
    !previewError &&
    totalLeadsPreview > 0;

  const fetchTemplates = async () => {
    if (!empresaId) return;
    try {
      setLoadingTemplates(true);
      const res = await api.get(`/empresas/${empresaId}/campanhas/templates`);
      setTemplates(res.data || []);
    } catch (err) {
      console.error("Erro ao carregar templates:", err);
    } finally {
      setLoadingTemplates(false);
    }
  };

  const fetchCampanhas = async () => {
    if (!empresaId) return;
    try {
      setLoadingCampanhas(true);
      const res = await api.get(`/empresas/${empresaId}/campanhas`);
      setCampanhas(res.data || []);
    } catch (err) {
      console.error("Erro ao carregar campanhas:", err);
    } finally {
      setLoadingCampanhas(false);
    }
  };

  const fetchTags = async () => {
    if (!empresaId) return;
    try {
      const res = await api.get(`/empresas/${empresaId}/crm/tags`);
      setTagsDisponiveis(res.data || []);
    } catch (err) {
      console.error("Erro ao carregar tags do CRM:", err);
    }
  };

  const fetchEmpresaConfig = async () => {
    if (!empresaId) return;
    try {
      const res = await api.get(`/empresas/${empresaId}`);
      setEmpresaConfig({
        disparo_delay_min: Number(res.data?.disparo_delay_min ?? 3),
        disparo_delay_max: Number(res.data?.disparo_delay_max ?? 7),
      });
    } catch (err) {
      console.error("Erro ao carregar configuração da empresa:", err);
      setEmpresaConfig({
        disparo_delay_min: 3,
        disparo_delay_max: 7,
      });
    }
  };

  useEffect(() => {
    fetchTemplates();
    fetchCampanhas();
    fetchTags();
    fetchEmpresaConfig();
  }, [empresaId]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  useEffect(() => {
    if (!showCampanhaModal || !empresaId) return undefined;
    if (!campanhaForm.template_id || !campanhaForm.tag) {
      setPreviewData(null);
      setPreviewError("");
      setLoadingPreview(false);
      return undefined;
    }

    let active = true;

    const fetchPreview = async () => {
      try {
        setLoadingPreview(true);
        setPreviewError("");
        const res = await api.get(`/empresas/${empresaId}/campanhas/preview`, {
          params: {
            template_id: campanhaForm.template_id,
            tag: campanhaForm.tag,
          },
        });
        if (!active) return;
        setPreviewData(res.data || null);
      } catch (err) {
        if (!active) return;
        console.error("Erro ao carregar pré-visualização:", err);
        setPreviewData(null);
        setPreviewError(err.response?.data?.detail || "Não foi possível gerar a pré-visualização.");
      } finally {
        if (active) {
          setLoadingPreview(false);
        }
      }
    };

    fetchPreview();

    return () => {
      active = false;
    };
  }, [showCampanhaModal, empresaId, campanhaForm.template_id, campanhaForm.tag]);

  const closeTemplateModal = () => {
    setShowTemplateModal(false);
    setEditingTemplate(null);
    setTemplateForm(initialTemplateForm);
  };

  const openCreateTemplateModal = () => {
    setEditingTemplate(null);
    setTemplateForm(initialTemplateForm);
    setShowTemplateModal(true);
  };

  const openEditTemplateModal = (template) => {
    setEditingTemplate(template);
    setTemplateForm({
      nome: template.nome || "",
      texto_template: template.texto_template || "",
    });
    setShowTemplateModal(true);
  };

  const handleSaveTemplate = async (e) => {
    e.preventDefault();
    try {
      setSavingTemplate(true);
      if (editingTemplate) {
        await api.put(`/empresas/${empresaId}/campanhas/templates/${editingTemplate.id}`, templateForm);
      } else {
        await api.post(`/empresas/${empresaId}/campanhas/templates`, templateForm);
      }
      await fetchTemplates();
      closeTemplateModal();
      setToast({
        type: "success",
        message: editingTemplate ? "Template atualizado com sucesso." : "Template criado com sucesso.",
      });
    } catch (err) {
      console.error("Erro ao salvar template:", err);
      setToast({
        type: "error",
        message: err.response?.data?.detail || "Não foi possível salvar o template.",
      });
    } finally {
      setSavingTemplate(false);
    }
  };

  const handleInsertTemplateVariable = (variableName) => {
    const textarea = templateTextareaRef.current;
    const token = `{{${variableName}}}`;

    if (!textarea) {
      setTemplateForm((prev) => ({ ...prev, texto_template: `${prev.texto_template || ""}${token}` }));
      return;
    }

    const start = textarea.selectionStart ?? 0;
    const end = textarea.selectionEnd ?? start;
    const currentValue = templateForm.texto_template || "";
    const nextValue = `${currentValue.slice(0, start)}${token}${currentValue.slice(end)}`;

    setTemplateForm((prev) => ({ ...prev, texto_template: nextValue }));

    window.requestAnimationFrame(() => {
      textarea.focus();
      const nextCursor = start + token.length;
      textarea.setSelectionRange(nextCursor, nextCursor);
    });
  };

  const handleDeleteTemplate = async (template) => {
    if (!window.confirm(`Excluir o template "${template.nome}"?`)) return;
    try {
      setDeletingTemplateId(template.id);
      await api.delete(`/empresas/${empresaId}/campanhas/templates/${template.id}`);
      await fetchTemplates();
      setToast({ type: "success", message: "Template excluído com sucesso." });
    } catch (err) {
      console.error("Erro ao excluir template:", err);
      setToast({
        type: "error",
        message: err.response?.data?.detail || "Não foi possível excluir o template.",
      });
    } finally {
      setDeletingTemplateId(null);
    }
  };

  const handleCreateCampanha = async (e) => {
    e.preventDefault();
    try {
      setSavingCampanha(true);
      await api.post(`/empresas/${empresaId}/campanhas`, {
        nome: campanhaForm.nome.trim(),
        template_id: campanhaForm.template_id,
        tags_alvo: campanhaForm.tag ? [campanhaForm.tag] : [],
      });
      await fetchCampanhas();
      setShowCampanhaModal(false);
      setCampanhaForm(initialCampanhaForm);
      setPreviewData(null);
      setPreviewError("");
      setToast({
        type: "success",
        message: "Campanha criada. O processamento foi iniciado em segundo plano.",
      });
    } catch (err) {
      console.error("Erro ao criar campanha:", err);
      setToast({
        type: "error",
        message: err.response?.data?.detail || "Não foi possível criar a campanha.",
      });
    } finally {
      setSavingCampanha(false);
    }
  };

  return (
    <div className="space-y-6">
      {toast ? (
        <div className="fixed right-6 top-6 z-[80]">
          <div
            className={`min-w-[320px] max-w-md rounded-2xl border px-4 py-3 shadow-xl ${
              toast.type === "success"
                ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                : "border-red-200 bg-red-50 text-red-800"
            }`}
          >
            <div className="flex items-start gap-3">
              <div className={`rounded-full p-1 ${toast.type === "success" ? "bg-emerald-100" : "bg-red-100"}`}>
                {toast.type === "success" ? <BellRing size={14} /> : <X size={14} />}
              </div>
              <div className="flex-1">
                <p className="text-sm font-semibold">
                  {toast.type === "success" ? "Tudo certo" : "Atenção"}
                </p>
                <p className="mt-0.5 text-sm">{toast.message}</p>
              </div>
              <button type="button" onClick={() => setToast(null)} className="rounded-lg p-1 opacity-70 hover:opacity-100">
                <X size={14} />
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="flex items-center gap-3 text-3xl font-bold tracking-tight text-gray-900">
            <Megaphone className="text-blue-600" size={30} />
            Campanhas
          </h1>
          <p className="mt-1 text-gray-500">
            Gerencie templates de mensagem e dispare campanhas em massa usando a conexão padrão da empresa.
          </p>
        </div>

        <div className="flex gap-2">
          <button
            type="button"
            onClick={fetchCampanhas}
            className="inline-flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
          >
            <RefreshCw size={16} />
            Atualizar
          </button>
          {activeTab === "templates" ? (
            <button
              type="button"
              onClick={openCreateTemplateModal}
              className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700"
            >
              <Plus size={16} />
              Novo template
            </button>
          ) : (
            <button
              type="button"
              onClick={() => {
                setCampanhaForm(initialCampanhaForm);
                setPreviewData(null);
                setPreviewError("");
                setShowCampanhaModal(true);
              }}
              className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700"
            >
              <Send size={16} />
              Novo Disparo
            </button>
          )}
        </div>
      </div>

      <div className="rounded-2xl border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-100 px-6 pt-4">
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setActiveTab("templates")}
              className={`rounded-t-xl px-4 py-2 text-sm font-medium transition-colors ${
                activeTab === "templates"
                  ? "border border-b-white border-blue-200 bg-blue-50 text-blue-700"
                  : "text-gray-500 hover:bg-gray-50 hover:text-gray-700"
              }`}
            >
              Templates
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("disparos")}
              className={`rounded-t-xl px-4 py-2 text-sm font-medium transition-colors ${
                activeTab === "disparos"
                  ? "border border-b-white border-blue-200 bg-blue-50 text-blue-700"
                  : "text-gray-500 hover:bg-gray-50 hover:text-gray-700"
              }`}
            >
              Disparos
            </button>
          </div>
        </div>

        <div className="p-6">
          {activeTab === "templates" ? (
            loadingTemplates ? (
              <div className="flex items-center justify-center py-14 text-gray-500">
                <RefreshCw size={18} className="mr-2 animate-spin" />
                Carregando templates...
              </div>
            ) : templatesEmpty ? (
              <div className="rounded-2xl border-2 border-dashed border-gray-200 bg-gray-50 px-6 py-14 text-center">
                <FileText className="mx-auto mb-3 text-gray-300" size={36} />
                <p className="text-sm font-medium text-gray-700">Nenhum template cadastrado.</p>
                <p className="mt-1 text-sm text-gray-500">
                  Crie mensagens reutilizáveis com variáveis como {`{{nome}}`} e {`{{telefone}}`}.
                </p>
              </div>
            ) : (
              <div className="grid gap-4 xl:grid-cols-2">
                {templates.map((template) => (
                  <div key={template.id} className="rounded-2xl border border-gray-200 bg-gray-50/70 p-5">
                    <div className="mb-4 flex items-start justify-between gap-4">
                      <div>
                        <h3 className="text-lg font-semibold text-gray-900">{template.nome}</h3>
                        <p className="mt-1 text-xs text-gray-500">Criado em {formatDate(template.criado_em)}</p>
                      </div>
                      <div className="flex items-center gap-2">
                        <button
                          type="button"
                          onClick={() => openEditTemplateModal(template)}
                          className="inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-white px-3 py-2 text-sm text-blue-700 transition-colors hover:bg-blue-50"
                        >
                          <Pencil size={14} />
                          Editar
                        </button>
                        <button
                          type="button"
                          onClick={() => handleDeleteTemplate(template)}
                          disabled={deletingTemplateId === template.id}
                          className="inline-flex items-center gap-2 rounded-lg border border-red-200 bg-white px-3 py-2 text-sm text-red-600 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60"
                        >
                          {deletingTemplateId === template.id ? <RefreshCw size={14} className="animate-spin" /> : <Trash2 size={14} />}
                          Excluir
                        </button>
                      </div>
                    </div>

                    <div className="mb-4 rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm leading-6 text-gray-700">
                      {template.texto_template}
                    </div>

                    <div className="flex flex-wrap gap-2">
                      {(template.variaveis_esperadas || []).length > 0 ? (
                        template.variaveis_esperadas.map((variavel) => (
                          <span
                            key={variavel}
                            className="rounded-full border border-violet-200 bg-violet-50 px-3 py-1 text-xs font-medium text-violet-700"
                          >
                            {`{{${variavel}}}`}
                          </span>
                        ))
                      ) : (
                        <span className="text-sm text-gray-400">Sem variáveis detectadas.</span>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )
          ) : loadingCampanhas ? (
            <div className="flex items-center justify-center py-14 text-gray-500">
              <RefreshCw size={18} className="mr-2 animate-spin" />
              Carregando campanhas...
            </div>
          ) : campanhasEmpty ? (
            <div className="rounded-2xl border-2 border-dashed border-gray-200 bg-gray-50 px-6 py-14 text-center">
              <Megaphone className="mx-auto mb-3 text-gray-300" size={36} />
              <p className="text-sm font-medium text-gray-700">Nenhum disparo realizado ainda.</p>
              <p className="mt-1 text-sm text-gray-500">
                Crie uma campanha selecionando um template e uma tag do CRM.
              </p>
            </div>
          ) : (
            <div className="overflow-x-auto rounded-2xl border border-gray-200">
              <table className="min-w-full divide-y divide-gray-200 text-left text-sm">
                <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
                  <tr>
                    <th className="px-4 py-3 font-semibold">Campanha</th>
                    <th className="px-4 py-3 font-semibold">Template</th>
                    <th className="px-4 py-3 font-semibold">Tag</th>
                    <th className="px-4 py-3 font-semibold">Agendamento</th>
                    <th className="px-4 py-3 font-semibold">Status</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 bg-white">
                  {campanhas.map((campanha) => (
                    <tr key={campanha.id}>
                      <td className="px-4 py-3 font-medium text-gray-900">{campanha.nome}</td>
                      <td className="px-4 py-3 text-gray-700">{campanha.template_nome || "-"}</td>
                      <td className="px-4 py-3 text-gray-700">
                        {(campanha.tags_alvo || []).length > 0 ? campanha.tags_alvo.join(", ") : "Todas"}
                      </td>
                      <td className="px-4 py-3 text-gray-600">{formatDate(campanha.data_agendamento || campanha.criado_em)}</td>
                      <td className="px-4 py-3"><StatusBadge status={campanha.status} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {showTemplateModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-2xl rounded-2xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
              <div>
                <h2 className="text-xl font-bold text-gray-900">
                  {editingTemplate ? "Editar template" : "Novo template"}
                </h2>
                <p className="text-sm text-gray-500">Use variáveis como {`{{nome}}`} e {`{{telefone}}`} no texto.</p>
              </div>
              <button type="button" onClick={closeTemplateModal} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100 hover:text-gray-700">
                <X size={18} />
              </button>
            </div>

            <form onSubmit={handleSaveTemplate} className="space-y-5 p-6">
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Nome do template</label>
                <input
                  required
                  value={templateForm.nome}
                  onChange={(e) => setTemplateForm((prev) => ({ ...prev, nome: e.target.value }))}
                  className="w-full rounded-xl border border-gray-200 px-4 py-2.5 text-gray-800 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                  placeholder="Ex: Reativação, Oferta VIP, Cobrança"
                />
              </div>

              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Texto do template</label>
                <textarea
                  ref={templateTextareaRef}
                  required
                  rows={8}
                  value={templateForm.texto_template}
                  onChange={(e) => setTemplateForm((prev) => ({ ...prev, texto_template: e.target.value }))}
                  className="w-full rounded-xl border border-gray-200 px-4 py-3 text-gray-800 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                  placeholder={"Olá {{nome}}, tudo bem?\nTemos uma condição especial para você hoje."}
                />
              </div>

              <div className="rounded-xl border border-blue-100 bg-blue-50 px-4 py-3 text-sm text-blue-800">
                <p className="mb-3">
                  Dica: além de {`{{nome}}`} e {`{{telefone}}`}, você pode usar campos existentes em `dados_adicionais` do lead.
                </p>
                <div className="flex flex-wrap gap-2">
                  {suggestedTemplateVariables.map((variableName) => (
                    <button
                      key={variableName}
                      type="button"
                      onClick={() => handleInsertTemplateVariable(variableName)}
                      className="rounded-full border border-blue-200 bg-white px-3 py-1 text-xs font-semibold text-blue-700 transition-colors hover:bg-blue-100"
                    >
                      {`{{${variableName}}}`}
                    </button>
                  ))}
                </div>
              </div>

              <div className="flex justify-end gap-3 pt-2">
                <button type="button" onClick={closeTemplateModal} className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm font-medium text-gray-600 hover:bg-gray-50">
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={savingTemplate}
                  className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {savingTemplate ? "Salvando..." : editingTemplate ? "Salvar alterações" : "Criar template"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}

      {showCampanhaModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-xl rounded-2xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
              <div>
                <h2 className="text-xl font-bold text-gray-900">Novo Disparo</h2>
                <p className="text-sm text-gray-500">Selecione o template e a tag para iniciar a campanha.</p>
              </div>
              <button
                type="button"
                onClick={() => {
                  setShowCampanhaModal(false);
                  setPreviewData(null);
                  setPreviewError("");
                }}
                className="rounded-lg p-2 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
              >
                <X size={18} />
              </button>
            </div>

            <form onSubmit={handleCreateCampanha} className="space-y-5 p-6">
              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Nome da campanha</label>
                <input
                  required
                  value={campanhaForm.nome}
                  onChange={(e) => setCampanhaForm((prev) => ({ ...prev, nome: e.target.value }))}
                  className="w-full rounded-xl border border-gray-200 px-4 py-2.5 text-gray-800 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                  placeholder="Ex: Reativação de leads frios"
                />
              </div>

              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Template</label>
                <select
                  required
                  value={campanhaForm.template_id}
                  onChange={(e) => setCampanhaForm((prev) => ({ ...prev, template_id: e.target.value }))}
                  className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-gray-800 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">Selecione um template</option>
                  {templates.map((template) => (
                    <option key={template.id} value={template.id}>
                      {template.nome}
                    </option>
                  ))}
                </select>
              </div>

              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Tag alvo</label>
                <select
                  required
                  value={campanhaForm.tag}
                  onChange={(e) => setCampanhaForm((prev) => ({ ...prev, tag: e.target.value }))}
                  className="w-full rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-gray-800 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                >
                  <option value="">Selecionar tag</option>
                  {tagsDisponiveis.map((tag) => (
                    <option key={tag} value={tag}>
                      {tag}
                    </option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-gray-500">
                  O disparo será enviado para leads que possuam essa tag no CRM.
                </p>
              </div>

              <div className="rounded-2xl border border-emerald-100 bg-emerald-50/70 p-4">
                <div className="mb-3 flex items-center justify-between gap-3">
                  <div>
                    <p className="text-sm font-semibold text-emerald-900">Pré-visualização</p>
                    <p className="text-xs text-emerald-700">
                      Assim a mensagem deve chegar ao lead antes do disparo real.
                    </p>
                  </div>
                  {loadingPreview ? (
                    <div className="inline-flex items-center gap-2 text-xs font-medium text-emerald-700">
                      <RefreshCw size={14} className="animate-spin" />
                      Gerando preview...
                    </div>
                  ) : null}
                </div>

                {!campanhaForm.template_id || !campanhaForm.tag ? (
                  <div className="rounded-2xl border border-dashed border-emerald-200 bg-white/80 px-4 py-5 text-sm text-emerald-800">
                    Selecione um template e uma tag para visualizar a mensagem renderizada.
                  </div>
                ) : previewError ? (
                  <div className="rounded-2xl border border-red-200 bg-red-50 px-4 py-4 text-sm text-red-700">
                    {previewError}
                  </div>
                ) : previewData ? (
                  <div className="space-y-3">
                    <div className="flex justify-end">
                      <div className="max-w-[85%] rounded-2xl rounded-br-md bg-[#DCF8C6] px-4 py-3 text-sm leading-6 text-gray-800 shadow-sm">
                        {previewData.preview_texto || "O template não gerou conteúdo para este lead."}
                      </div>
                    </div>
                    <div
                      className={`rounded-xl border px-4 py-3 text-sm ${
                        totalLeadsPreview > 0
                          ? "border-blue-200 bg-blue-50 text-blue-800"
                          : "border-amber-200 bg-amber-50 text-amber-800"
                      }`}
                    >
                      {totalLeadsPreview > 0
                        ? `👥 Este disparo será enviado para ${totalLeadsPreview} contatos.`
                        : "Nenhum contato com essa tag foi encontrado. O envio foi bloqueado por segurança."}
                    </div>
                    {totalLeadsPreview > 0 ? (
                      <div className="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3 text-sm text-slate-700">
                        {`⏳ Estimativa de envio: ${estimativaEnvio}.`}
                      </div>
                    ) : null}
                    <p className="text-xs text-emerald-800">
                      {previewData.usou_mock
                        ? "A visualização está usando dados mockados porque ainda não há leads com essa tag."
                        : "A visualização foi gerada a partir de um lead real com a tag selecionada."}
                    </p>
                  </div>
                ) : (
                  <div className="rounded-2xl border border-dashed border-emerald-200 bg-white/80 px-4 py-5 text-sm text-emerald-800">
                    Aguardando dados para gerar a pré-visualização.
                  </div>
                )}
              </div>

              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={() => {
                    setShowCampanhaModal(false);
                    setPreviewData(null);
                    setPreviewError("");
                  }}
                  className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm font-medium text-gray-600 hover:bg-gray-50"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={savingCampanha || !campanhaPodeEnviar}
                  className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {savingCampanha ? "Enviando..." : "Enviar disparo"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </div>
  );
}

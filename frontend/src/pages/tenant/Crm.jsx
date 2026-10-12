import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { useLocation } from "react-router-dom";
import api from "../../services/api";
import { Users, Plus, Phone, Clock, Upload, X, Trash2, GripVertical, RefreshCw } from "lucide-react";
import { getActiveEmpresaId, getStoredToken, getStoredUser } from "../../utils/auth";
import { CRM_LEAD_SAVE_TIMEOUT_MS, confirmedCrmLeadResponse, crmSaveError } from "../../utils/crmLeadSave";
import { normalizeLeadTags } from "../../utils/leadTags";
import LeadTagsEditor from "../../components/LeadTagsEditor";
import LeadDetailsModal from "../../components/LeadDetailsModal";
import { CRM_ETAPAS_ATUALIZADAS } from "../../utils/events";
import { ActionButton, EmptyState, LoadingState, PageHeader } from "../../components/ui";
import "../../styles/crm-workspace.css";

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

/** MIME type do payload de arrastar lead entre colunas (evita colisão com texto selecionado). */
const CRM_LEAD_DRAG_MIME = "application/x-agenteos-crm-lead+json";

export default function Crm() {
  const location = useLocation();
  const [funil, setFunil] = useState(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState("");
  const [selectedLead, setSelectedLead] = useState(null);
  const [availableTags, setAvailableTags] = useState([]);
  const [showImportModal, setShowImportModal] = useState(false);
  const importDialogRef = useRef(null);
  const [importFile, setImportFile] = useState(null);
  const [importPreviewColumns, setImportPreviewColumns] = useState([]);
  const [importSelectedTags, setImportSelectedTags] = useState([]);
  const [importSaving, setImportSaving] = useState(false);
  const [importInfo, setImportInfo] = useState("");
  const [toastMessage, setToastMessage] = useState("");
  /** Lead em processo de mudança de etapa (evita arrastar duplicado). */
  const [movingLeadId, setMovingLeadId] = useState(null);
  /** Coluna sobre a qual o utilizador está a arrastar (feedback visual). */
  const [dragOverEtapaId, setDragOverEtapaId] = useState(null);

  const user = getStoredUser();
  const empresaId = getActiveEmpresaId();
  const leadSaveScopeRef = useRef(null);
  const editorEmpresaRef = useRef(empresaId);

  useLayoutEffect(() => {
    if (editorEmpresaRef.current !== empresaId) {
      editorEmpresaRef.current = empresaId;
      setSelectedLead(null);
    }
    leadSaveScopeRef.current = { empresaId, leadId: selectedLead?.id };
    return () => { leadSaveScopeRef.current = null; };
  }, [empresaId, selectedLead?.id]);

  /**
   * Colunas do Kanban = apenas `etapas` do GET /empresas/:id/crm (tabela CRMEtapa).
   * Não usar `colunas_tags` nem constantes fixas — espelho do banco, ordenado por `ordem`.
   */
  const etapasOrdenadas = useMemo(() => {
    const lista = funil?.etapas;
    if (!Array.isArray(lista) || lista.length === 0) return [];
    return [...lista].sort(
      (a, b) =>
        Number(a?.ordem ?? 0) - Number(b?.ordem ?? 0) ||
        String(a?.id || "").localeCompare(String(b?.id || ""), "pt-BR"),
    );
  }, [funil]);

  const fetchCrmData = useCallback(async () => {
    if (!empresaId) return;
    try {
      setLoading(true);
      setLoadError("");
      const res = await api.get(`/empresas/${empresaId}/crm`);
      const raw = res.data;
      const etapasApi = Array.isArray(raw?.etapas) ? raw.etapas : [];
      setFunil({
        funil_id: raw?.funil_id ?? null,
        funil_nome: raw?.funil_nome ?? "",
        etapas: etapasApi,
        colunas_tags: Array.isArray(raw?.colunas_tags) ? raw.colunas_tags : [],
      });
    } catch (err) {
      console.error("Erro ao buscar dados do CRM:", err);
      setLoadError("Não foi possível carregar o CRM. Tente novamente.");
    } finally {
      setLoading(false);
    }
  }, [empresaId]);

  const fetchOfficialTags = useCallback(async () => {
    if (!empresaId) return;
    try {
      const res = await api.get(`/empresas/${empresaId}/crm/tags/oficiais`);
      setAvailableTags(res.data || []);
    } catch (err) {
      console.error("Erro ao carregar tags oficiais:", err);
      setAvailableTags([]);
    }
  }, [empresaId]);

  useEffect(() => {
    if (empresaId) {
      void fetchCrmData();
      void fetchOfficialTags();
    }
  }, [empresaId, location.key, fetchCrmData, fetchOfficialTags]);

  /** Recarrega etapas/nomes ao voltar ao separador (ex.: após renomear etapa nas configurações). */
  useEffect(() => {
    const onVisible = () => {
      if (document.visibilityState === "visible" && empresaId) void fetchCrmData();
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, [empresaId, fetchCrmData]);

  /** Super Admin alterou etapas: mesmo separador (CustomEvent) ou outro separador (storage). */
  useEffect(() => {
    if (!empresaId) return undefined;
    const chave = `crm_etapas_revision_${empresaId}`;
    const onCustom = (ev) => {
      if (String(ev?.detail?.empresaId || "") !== String(empresaId)) return;
      void fetchCrmData();
    };
    const onStorage = (ev) => {
      if (ev.key !== chave) return;
      void fetchCrmData();
    };
    window.addEventListener(CRM_ETAPAS_ATUALIZADAS, onCustom);
    window.addEventListener("storage", onStorage);
    return () => {
      window.removeEventListener(CRM_ETAPAS_ATUALIZADAS, onCustom);
      window.removeEventListener("storage", onStorage);
    };
  }, [empresaId, fetchCrmData]);

  useEffect(() => {
    if (!showImportModal) return undefined;
    const previousFocus = document.activeElement;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    const frame = requestAnimationFrame(() => importDialogRef.current?.querySelector("input[type='file']")?.focus());
    const handleKeyDown = (event) => {
      const dialog = importDialogRef.current;
      if (event.key === "Escape") {
        event.preventDefault();
        dialog?.querySelector("[data-import-close]")?.click();
      }
      if (event.key !== "Tab") return;
      const items = [...(dialog?.querySelectorAll("button:not([disabled]), input:not([disabled])") || [])]
        .filter((item) => item.getClientRects().length > 0 && getComputedStyle(item).visibility !== "hidden");
      const first = items[0];
      const last = items.at(-1);
      if (!dialog?.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first)?.focus();
      } else if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last?.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first?.focus();
      }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      cancelAnimationFrame(frame);
      document.removeEventListener("keydown", handleKeyDown);
      document.body.style.overflow = previousOverflow;
      if (previousFocus?.isConnected) previousFocus.focus({ preventScroll: true });
    };
  }, [showImportModal]);

  const showToast = useCallback((message) => {
    setToastMessage(message);
    window.setTimeout(() => setToastMessage(""), 2800);
  }, []);

  const updateLeadInState = useCallback((leadId, updates) => {
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
  }, []);

  const moveLeadOptimisticToEtapa = useCallback((leadId, targetEtapaId) => {
    setFunil((prev) => {
      if (!prev?.etapas?.length) return prev;
      let payload = null;
      for (const e of prev.etapas) {
        const hit = (e.leads || []).find((l) => l?.id === leadId);
        if (hit) {
          payload = hit;
          break;
        }
      }
      if (!payload) return prev;
      return {
        ...prev,
        etapas: prev.etapas.map((e) => {
          const leadsSem = (e.leads || []).filter((l) => l?.id !== leadId);
          if (e.id === targetEtapaId) return { ...e, leads: [...leadsSem, payload] };
          return { ...e, leads: leadsSem };
        }),
      };
    });
  }, []);

  const persistLeadEtapa = useCallback(
    (leadId, targetEtapaId) =>
      api.put(`/empresas/${empresaId}/crm/leads/${leadId}`, { etapa_id: targetEtapaId }),
    [empresaId]
  );

  const handleLeadDragStart = useCallback(
    (e, leadId) => {
      if (movingLeadId || !leadId) {
        e.preventDefault();
        return;
      }
      try {
        e.dataTransfer.setData(CRM_LEAD_DRAG_MIME, JSON.stringify({ leadId: String(leadId) }));
        e.dataTransfer.effectAllowed = "move";
      } catch {
        e.preventDefault();
      }
    },
    [movingLeadId]
  );

  const handleEtapaDragOver = useCallback((e, etapaId) => {
    const types = Array.from(e.dataTransfer?.types || []);
    if (!types.includes(CRM_LEAD_DRAG_MIME)) return;
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
    setDragOverEtapaId(etapaId);
  }, []);

  const handleEtapaDragLeave = useCallback((e) => {
    const next = e.relatedTarget;
    if (next && e.currentTarget.contains(next)) return;
    setDragOverEtapaId(null);
  }, []);

  const handleEtapaDrop = useCallback(
    async (e, targetEtapaId) => {
      e.preventDefault();
      setDragOverEtapaId(null);
      let leadId = "";
      try {
        const raw = e.dataTransfer.getData(CRM_LEAD_DRAG_MIME);
        const parsed = raw ? JSON.parse(raw) : {};
        leadId = String(parsed.leadId || "").trim();
      } catch {
        return;
      }
      if (!leadId || !targetEtapaId) return;

      const origem = (funil?.etapas || []).find((col) => (col.leads || []).some((l) => l?.id === leadId));
      const origemId = origem?.id;
      if (origemId === targetEtapaId) return;

      setMovingLeadId(leadId);
      moveLeadOptimisticToEtapa(leadId, targetEtapaId);
      try {
        const { data } = await persistLeadEtapa(leadId, targetEtapaId);
        if (data?.tags) {
          updateLeadInState(leadId, { tags: data.tags });
        }
        showToast("Lead movido para a nova etapa.");
      } catch (err) {
        console.error("Erro ao mover lead de etapa:", err);
        await fetchCrmData();
        alert(err.response?.data?.detail || "Não foi possível mover o lead. Os dados foram recarregados.");
      } finally {
        setMovingLeadId(null);
      }
    },
    [funil, moveLeadOptimisticToEtapa, persistLeadEtapa, fetchCrmData, updateLeadInState, showToast]
  );

  const handleLeadTagsChange = async (leadId, nextTags) => {
    try {
      // Salva na API
      await api.put(`/empresas/${empresaId}/crm/leads/${leadId}`, { tags: nextTags });
      
      // Atualiza o estado local apenas com a mudança (o updateLeadInState já faz o merge seguro)
      updateLeadInState(leadId, { tags: nextTags });
      showToast("Tags atualizadas com sucesso!");
    } catch (error) {
      console.error("Erro ao salvar tags no banco:", error);
      alert("Erro ao salvar tags.");
    }
  };

  const handleLeadSave = async (leadId, updates) => {
    const scope = leadSaveScopeRef.current;
    const token = getStoredToken();
    if (!scope || scope.empresaId !== empresaId || scope.leadId !== leadId || getActiveEmpresaId() !== empresaId) {
      throw crmSaveError('CRM_CONTEXT_CHANGED');
    }
    const response = await api.put(`/empresas/${empresaId}/crm/leads/${leadId}`, updates, {
      timeout: CRM_LEAD_SAVE_TIMEOUT_MS,
      headers: { Authorization: token ? `Bearer ${token}` : '' },
      crmFieldsSaveContext: { empresaId, leadId },
    });
    if (leadSaveScopeRef.current !== scope || getActiveEmpresaId() !== empresaId || getStoredToken() !== token) {
      throw crmSaveError('CRM_CONTEXT_CHANGED');
    }
    if (!confirmedCrmLeadResponse(response, leadId)) throw crmSaveError('CRM_SAVE_NOT_CONFIRMED');
    // Merge only submitted fields: never replace stage/tags with response data.
    updateLeadInState(leadId, updates);
    showToast("Lead atualizado com sucesso!");
    return { saved: true, empresaId, leadId };
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

  if (!user) return <EmptyState title="Sessão expirada" description="Faça login novamente para acessar o CRM." />;

  return (
    <div className="ux04-crm">
      {toastMessage ? (
        <div role="status" className="ux04-crm-toast">
          {toastMessage}
        </div>
      ) : null}

      <PageHeader
        title="Leads / CRM"
        description={
          funil
            ? `Funil: ${funil.funil_nome}. Acompanhe contatos e oportunidades em cada etapa.`
            : "Gerencie os leads capturados pela IA e mova oportunidades entre etapas."
        }
        icon={Users}
        eyebrow="Pipeline"
        actions={
          <>
          <ActionButton
            type="button"
            onClick={() => setShowImportModal(true)}
            variant="secondary"
            icon={Upload}
          >
            Importar leads
          </ActionButton>
          <ActionButton
            type="button"
            onClick={() => alert("Criação manual de Lead em breve! A IA já faz isso automaticamente.")}
            icon={Plus}
          >
            Novo lead
          </ActionButton>
          </>
        }
      />

      <div className="ux04-crm-toolbar">
        <span>{etapasOrdenadas.reduce((total, etapa) => total + (etapa.leads?.length || 0), 0)} contatos · {etapasOrdenadas.length} etapas</span>
        <span role="status" className={movingLeadId ? "ux04-crm-moving" : ""}>
          {movingLeadId ? <><RefreshCw size={14} className="animate-spin" aria-hidden="true" /> Atualizando etapa…</> : "Arraste um contato para mudar a etapa"}
        </span>
      </div>

      {loadError ? <div className="ux04-crm-error" role="alert"><span>{loadError}</span><ActionButton type="button" variant="secondary" onClick={fetchCrmData}>Tentar novamente</ActionButton></div> : null}
      {loading ? (
        <LoadingState label="Carregando funil..." />
      ) : loadError && !funil ? null : !funil || !Array.isArray(funil.etapas) || etapasOrdenadas.length === 0 ? (
        <EmptyState title="Funil sem etapas" description="Nenhum dado de CRM encontrado ou o funil ainda não tem etapas. Configure o funil para esta empresa." />
      ) : (
        <div className="ux04-crm-board custom-scrollbar" role="region" aria-label="Quadro de etapas do CRM" tabIndex={0}>
          <div className="ux04-crm-columns">
            {etapasOrdenadas.map((etapa) => {
              const etapaId = etapa?.id;
              if (!etapaId) return null;
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
                  key={etapaId}
                  role="region"
                  aria-label={`Etapa ${etapa.nome}`}
                  onDragOver={(ev) => handleEtapaDragOver(ev, etapaId)}
                  onDragLeave={handleEtapaDragLeave}
                  onDrop={(ev) => handleEtapaDrop(ev, etapaId)}
                  data-etapa-id={etapaId}
                  className={`ux04-crm-column ${dragOverEtapaId === etapaId ? "is-drop-target" : ""}`}
                >
                  <div className="ux04-crm-column-header">
                    <div className="min-w-0">
                      <h3 className="ux04-crm-column-title" title={etapa.nome}>
                        {etapa.nome}
                      </h3>
                      <p
                        className={`ux04-crm-column-value ${totalLeadsConvertidosEtapa > 0 ? "has-value" : ""}`}
                      >
                        {resumoFaturamentoEtapa}
                      </p>
                    </div>
                    <span className="ux04-crm-column-count" aria-label={`${etapa.leads?.length || 0} contatos`}>
                      {etapa.leads?.length || 0}
                    </span>
                  </div>

                  {dragOverEtapaId === etapaId ? <p className="ux04-crm-drop-hint" role="status">Solte para mover para {etapa.nome}</p> : null}
                  <div className="ux04-crm-column-body custom-scrollbar">
                    {!etapa.leads || etapa.leads.length === 0 ? (
                      <div className="ux04-crm-column-empty">
                        Nenhum lead nesta etapa
                      </div>
                    ) : (
                      etapa.leads.map((lead) => (
                        <div
                          key={lead.id}
                          role="button"
                          aria-label={`Abrir detalhes de ${lead.nome_contato || "contato"}`}
                          aria-busy={movingLeadId === lead.id}
                          data-lead-id={lead.id}
                          tabIndex={0}
                          draggable={!movingLeadId}
                          onDragStart={(ev) => handleLeadDragStart(ev, lead.id)}
                          onDragEnd={() => setDragOverEtapaId(null)}
                          onClick={(ev) => {
                            ev.currentTarget.focus({ preventScroll: true });
                            setSelectedLead(lead);
                          }}
                          onKeyDown={(ev) => {
                            if (ev.target === ev.currentTarget && (ev.key === "Enter" || ev.key === " ")) {
                              ev.preventDefault();
                              setSelectedLead(lead);
                            }
                          }}
                          className={`ux04-crm-card ${movingLeadId === lead.id ? "is-moving" : ""} ${!movingLeadId ? "can-drag" : "is-waiting"}`}
                        >
                          <div className="ux04-crm-card-heading">
                            <div className="flex min-w-0 flex-1 items-center gap-2">
                              <span
                                className="ux04-crm-grip"
                                title="Arrastar para outra etapa"
                                aria-hidden
                              >
                                <GripVertical size={16} />
                              </span>
                              <h4 className="ux04-crm-contact-name">{lead.nome_contato}</h4>
                            </div>
                            <div className="flex items-center gap-1">
                              <button
                                type="button"
                                aria-label={`Excluir lead ${lead.nome_contato || "contato"}`}
                                className="ux04-crm-delete"
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
                            <div className="ux04-crm-phone">
                              <Phone size={14} aria-hidden="true" />
                              <span>{lead.telefone}</span>
                            </div>
                          ) : null}

                          {lead.historico_resumo ? (
                            <p className="ux04-crm-summary">
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

                          <div className="ux04-crm-sale">
                            <span className="ux04-crm-sale-label">
                              Valor Venda
                            </span>
                            <span
                              className={`ux04-crm-sale-value ${Number(lead.valor_conversao || 0) > 0 ? "has-value" : ""}`}
                            >
                              {formatCurrency(lead.valor_conversao || 0)}
                            </span>
                          </div>

                          <div className="ux04-crm-card-footer">
                            <div className="ux04-crm-date">
                              <Clock size={12} />
                              {formatDate(lead.criado_em)}
                            </div>
                            <span className="ux04-crm-details">{movingLeadId === lead.id ? "Atualizando…" : "Detalhes →"}</span>
                          </div>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}

      <LeadDetailsModal
        key={`${empresaId}:${selectedLead?.id || 'none'}`}
        scopeId={empresaId}
        open={Boolean(selectedLead)}
        lead={selectedLead}
        onClose={() => setSelectedLead(null)}
        onSaveLead={handleLeadSave}
        onSaveTags={handleLeadTagsChange}
        availableTags={availableTags}
      />

      {showImportModal ? (
        <div className="ux04-lead-overlay">
          <div ref={importDialogRef} className="ux04-lead-dialog ux04-crm-import" role="dialog" aria-modal="true" aria-labelledby="ux04-import-title" aria-describedby="ux04-import-description">
            <div className="ux04-lead-header">
              <div>
                <h2 id="ux04-import-title" className="ux04-crm-import-title">Importar Leads</h2>
                <p id="ux04-import-description" className="ux04-lead-helper">Envie um CSV ou Excel com colunas de Nome e Telefone.</p>
              </div>
              <button type="button" onClick={resetImportModal} aria-label="Fechar importação" data-import-close className="ux04-lead-close">
                <X size={18} />
              </button>
            </div>

            <form onSubmit={handleSubmitImport} className="ux04-lead-form" aria-busy={importSaving}>
              <div className="ux04-lead-body">
                <div className="space-y-5">
                  <div>
                    <label htmlFor="ux04-import-file" className="ux04-crm-import-label">Arquivo</label>
                    <input
                      id="ux04-import-file"
                      type="file"
                      accept=".csv,.xlsx"
                      onChange={handleImportFileChange}
                      className="fm-field"
                    />
                  </div>

                  <div className="ux04-crm-import-section">
                    <p className="ux04-crm-import-label">Preview das colunas</p>
                    {importPreviewColumns.length > 0 ? (
                      <div className="flex flex-wrap gap-2">
                        {importPreviewColumns.map((column) => (
                          <span key={column} className="fm-badge fm-tone-info">
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

                  <div className="ux04-crm-import-section">
                    <p className="ux04-crm-import-label">Tags iniciais da lista</p>
                    <div className="flex flex-wrap gap-2">
                      {availableTags.map((tag) => {
                        const selected = importSelectedTags.includes(tag.nome);
                        return (
                          <button
                            key={tag.id}
                            type="button"
                            aria-pressed={selected}
                            onClick={() =>
                              setImportSelectedTags((prev) =>
                                selected ? prev.filter((item) => item !== tag.nome) : [...prev, tag.nome]
                              )
                            }
                            className={`ux04-crm-import-tag ${selected ? "is-selected" : ""}`}
                          >
                            {tag.nome}
                          </button>
                        );
                      })}
                    </div>
                  </div>
                </div>
              </div>
              <div className="ux04-lead-footer justify-end">
                <button type="button" onClick={resetImportModal} className="fm-button fm-button-secondary">
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={!importFile || importSaving}
                  className="fm-button fm-button-primary"
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

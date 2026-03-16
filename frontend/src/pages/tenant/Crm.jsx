import { useEffect, useState } from "react";
import api from "../../services/api";
import { Users, Plus, Phone, Clock, MessageSquare, X } from "lucide-react";
import { getActiveEmpresaId, getStoredUser } from "../../utils/auth";
import LeadTagsEditor from "../../components/LeadTagsEditor";

function formatDate(value) {
  if (!value) return "Hoje";
  return new Date(value).toLocaleDateString("pt-BR");
}

export default function Crm() {
  const [funil, setFunil] = useState(null);
  const [loading, setLoading] = useState(true);
  const [selectedLead, setSelectedLead] = useState(null);

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

  useEffect(() => {
    if (empresaId) fetchCrmData();
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

  const handleLeadTagsChange = async (leadId, nextTags) => {
    await api.put(`/empresas/${empresaId}/crm/leads/${leadId}`, { tags: nextTags });
    updateLeadInState(leadId, { tags: nextTags });
  };

  if (!user) return <div className="p-8 text-center text-gray-500">Faça login novamente.</div>;

  return (
    <div className="flex h-full flex-col space-y-6">
      <div className="flex flex-shrink-0 items-center justify-between">
        <div>
          <h1 className="flex items-center gap-3 text-3xl font-bold tracking-tight text-gray-900">
            <Users className="text-blue-600" size={32} />
            CRM Dinâmico
          </h1>
          <p className="mt-1 text-gray-500">
            {funil ? `Funil ativo: ${funil.funil_nome}` : "Gerencie seus leads capturados pela IA."}
          </p>
        </div>

        <button
          onClick={() => alert("Criação manual de Lead em breve! A IA já faz isso automaticamente.")}
          className="flex items-center space-x-2 rounded-lg bg-blue-600 px-4 py-2.5 font-medium text-white shadow-sm transition-colors hover:bg-blue-700"
        >
          <Plus size={20} />
          <span>Novo Lead</span>
        </button>
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
            {funil.etapas.map((etapa) => (
              <div
                key={etapa.id}
                className="flex w-80 flex-col overflow-hidden rounded-2xl border border-gray-200/60 bg-gray-50/80 shadow-sm"
              >
                <div className="flex cursor-move items-center justify-between border-b border-gray-200/60 bg-gray-100/50 px-4 py-3">
                  <h3 className="truncate font-semibold text-gray-800">{etapa.nome}</h3>
                  <span className="rounded-md border border-gray-200 bg-white px-2 py-1 text-xs font-bold text-gray-600 shadow-sm">
                    {etapa.leads?.length || 0}
                  </span>
                </div>

                <div className="custom-scrollbar flex-1 space-y-3 overflow-y-auto p-3 min-h-[150px]">
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
                          <button className="rounded-lg bg-blue-50 p-1.5 text-blue-600 opacity-0 transition-opacity group-hover:opacity-100">
                            <MessageSquare size={14} />
                          </button>
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
                          tags={lead.tags || []}
                          compact
                          placeholder="Nova tag + Enter"
                          onChange={(nextTags) => handleLeadTagsChange(lead.id, nextTags)}
                        />

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
            ))}

            <div className="flex min-h-[500px] w-80 flex-col items-center justify-center rounded-2xl border-2 border-dashed border-gray-200 opacity-50 transition-all hover:border-blue-300 hover:bg-gray-50 hover:opacity-100">
              <div className="flex items-center gap-2 pb-20 font-medium text-gray-500">
                <Plus size={20} />
                Nova Etapa
              </div>
            </div>
          </div>
        </div>
      )}

      {selectedLead ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-2xl rounded-2xl bg-white shadow-2xl">
            <div className="flex items-start justify-between border-b border-gray-100 px-6 py-4">
              <div>
                <h2 className="text-xl font-bold text-gray-900">{selectedLead.nome_contato}</h2>
                <p className="mt-1 text-sm text-gray-500">
                  Lead criado em {formatDate(selectedLead.criado_em)}
                </p>
              </div>
              <button
                type="button"
                onClick={() => setSelectedLead(null)}
                className="rounded-lg p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700"
              >
                <X size={18} />
              </button>
            </div>

            <div className="space-y-6 p-6">
              <div className="grid gap-4 md:grid-cols-2">
                <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">Telefone</p>
                  <p className="text-sm font-medium text-gray-800">{selectedLead.telefone || "Não informado"}</p>
                </div>
                <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                  <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-gray-500">Tags do lead</p>
                  <LeadTagsEditor
                    tags={selectedLead.tags || []}
                    placeholder="Adicionar tag e pressionar Enter"
                    onChange={(nextTags) => handleLeadTagsChange(selectedLead.id, nextTags)}
                  />
                </div>
              </div>

              <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Resumo do histórico</p>
                <p className="text-sm leading-6 text-gray-700">
                  {selectedLead.historico_resumo || "Nenhum resumo disponível para este lead."}
                </p>
              </div>

              <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
                <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">Dados adicionais</p>
                <pre className="overflow-x-auto whitespace-pre-wrap text-xs leading-6 text-gray-600">
                  {JSON.stringify(selectedLead.dados_adicionais || {}, null, 2)}
                </pre>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

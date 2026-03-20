import { useEffect, useMemo, useState } from "react";
import {
  ArrowRightLeft,
  BellRing,
  ClipboardList,
  MessageCircleMore,
  Pencil,
  Plus,
  RefreshCw,
  Send,
  Trash2,
  X,
} from "lucide-react";

import api from "../../services/api";
import { getActiveEmpresaId } from "../../utils/auth";

const initialForm = {
  nome_destino: "",
  contatos_destino: "",
  instrucoes_ativacao: "",
};

function parseContatos(rawValue) {
  return String(rawValue || "")
    .split(/\r?\n|,/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function formatDate(value) {
  if (!value) return "-";
  return new Date(value).toLocaleString("pt-BR");
}

export default function Transferencias() {
  const empresaId = getActiveEmpresaId();

  const [activeTab, setActiveTab] = useState("destinos");
  const [loadingDestinos, setLoadingDestinos] = useState(true);
  const [loadingHistorico, setLoadingHistorico] = useState(true);
  const [destinos, setDestinos] = useState([]);
  const [historico, setHistorico] = useState([]);
  const [showModal, setShowModal] = useState(false);
  const [editingDestino, setEditingDestino] = useState(null);
  const [formData, setFormData] = useState(initialForm);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [testingId, setTestingId] = useState(null);
  const [toast, setToast] = useState(null);

  const emptyHistorico = useMemo(() => !historico || historico.length === 0, [historico]);

  const fetchDestinos = async () => {
    if (!empresaId) return;
    try {
      setLoadingDestinos(true);
      const res = await api.get(`/empresas/${empresaId}/transferencias/destinos`);
      setDestinos(res.data || []);
    } catch (err) {
      console.error("Erro ao carregar destinos:", err);
    } finally {
      setLoadingDestinos(false);
    }
  };

  const fetchHistorico = async () => {
    if (!empresaId) return;
    try {
      setLoadingHistorico(true);
      const res = await api.get(`/empresas/${empresaId}/transferencias/historico`);
      setHistorico(res.data || []);
    } catch (err) {
      console.error("Erro ao carregar histórico de transferências:", err);
    } finally {
      setLoadingHistorico(false);
    }
  };

  useEffect(() => {
    fetchDestinos();
    fetchHistorico();
  }, [empresaId]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const openCreateModal = () => {
    setEditingDestino(null);
    setFormData(initialForm);
    setShowModal(true);
  };

  const openEditModal = (destino) => {
    setEditingDestino(destino);
    setFormData({
      nome_destino: destino.nome_destino || "",
      contatos_destino: (destino.contatos_destino || []).join("\n"),
      instrucoes_ativacao: destino.instrucoes_ativacao || "",
    });
    setShowModal(true);
  };

  const closeModal = () => {
    setShowModal(false);
    setEditingDestino(null);
    setFormData(initialForm);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      setSaving(true);
      const payload = {
        nome_destino: formData.nome_destino.trim(),
        contatos_destino: parseContatos(formData.contatos_destino),
        instrucoes_ativacao: formData.instrucoes_ativacao.trim(),
      };

      if (editingDestino) {
        await api.put(`/empresas/${empresaId}/transferencias/destinos/${editingDestino.id}`, payload);
      } else {
        await api.post(`/empresas/${empresaId}/transferencias/destinos`, payload);
      }

      await fetchDestinos();
      closeModal();
    } catch (err) {
      console.error("Erro ao salvar destino:", err);
      alert(err.response?.data?.detail || "Erro ao salvar destino de transferência.");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (destino) => {
    if (!window.confirm(`Excluir o destino "${destino.nome_destino}"?`)) return;
    try {
      setDeletingId(destino.id);
      await api.delete(`/empresas/${empresaId}/transferencias/destinos/${destino.id}`);
      await fetchDestinos();
    } catch (err) {
      console.error("Erro ao excluir destino:", err);
      alert(err.response?.data?.detail || "Erro ao excluir destino.");
    } finally {
      setDeletingId(null);
    }
  };

  const handleTestarDestino = async (destino) => {
    try {
      setTestingId(destino.id);
      const res = await api.post(`/empresas/${empresaId}/transferencias/destinos/${destino.id}/testar`);
      setToast({
        type: "success",
        message: res.data?.detail || "Teste enviado com sucesso.",
      });
    } catch (err) {
      console.error("Erro ao testar destino:", err);
      setToast({
        type: "error",
        message:
          err.response?.data?.detail ||
          "Falha ao enviar o teste. Verifique os números e a conexão configurada.",
      });
    } finally {
      setTestingId(null);
    }
  };

  return (
    <div className="space-y-6">
      {toast ? (
        <div className="fixed right-6 top-6 z-[70]">
          <div
            className={`min-w-[320px] max-w-md rounded-2xl border px-4 py-3 shadow-xl ${
              toast.type === "success"
                ? "border-emerald-200 bg-emerald-50 text-emerald-800"
                : "border-red-200 bg-red-50 text-red-800"
            }`}
          >
            <div className="flex items-start gap-3">
              <div
                className={`mt-0.5 rounded-full p-1 ${
                  toast.type === "success" ? "bg-emerald-100" : "bg-red-100"
                }`}
              >
                {toast.type === "success" ? <BellRing size={16} /> : <X size={16} />}
              </div>
              <div className="flex-1">
                <p className="text-sm font-semibold">
                  {toast.type === "success" ? "Teste enviado" : "Falha no teste"}
                </p>
                <p className="mt-0.5 text-sm">{toast.message}</p>
              </div>
              <button
                type="button"
                onClick={() => setToast(null)}
                className="rounded-lg p-1 opacity-70 transition-opacity hover:opacity-100"
              >
                <X size={14} />
              </button>
            </div>
          </div>
        </div>
      ) : null}

      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="flex items-center gap-3 text-3xl font-bold tracking-tight text-gray-900">
            <ArrowRightLeft className="text-blue-600" size={30} />
            Transferências
          </h1>
          <p className="mt-1 text-gray-500">
            Configure destinos de transbordo e acompanhe o histórico auditável das transferências.
          </p>
        </div>

        <div className="flex gap-2">
          <button
            type="button"
            onClick={fetchHistorico}
            className="inline-flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
          >
            <RefreshCw size={16} />
            Atualizar histórico
          </button>
          <button
            type="button"
            onClick={openCreateModal}
            className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700"
          >
            <Plus size={16} />
            Novo destino
          </button>
        </div>
      </div>

      <div className="rounded-2xl border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-100 px-6 pt-4">
          <div className="flex gap-2">
            <button
              type="button"
              onClick={() => setActiveTab("destinos")}
              className={`rounded-t-xl px-4 py-2 text-sm font-medium transition-colors ${
                activeTab === "destinos"
                  ? "border border-b-white border-blue-200 bg-blue-50 text-blue-700"
                  : "text-gray-500 hover:bg-gray-50 hover:text-gray-700"
              }`}
            >
              Destinos
            </button>
            <button
              type="button"
              onClick={() => setActiveTab("historico")}
              className={`rounded-t-xl px-4 py-2 text-sm font-medium transition-colors ${
                activeTab === "historico"
                  ? "border border-b-white border-blue-200 bg-blue-50 text-blue-700"
                  : "text-gray-500 hover:bg-gray-50 hover:text-gray-700"
              }`}
            >
              Histórico (Auditoria)
            </button>
          </div>
        </div>

        <div className="p-6">
          {activeTab === "destinos" ? (
            <div className="space-y-4">
              {loadingDestinos ? (
                <div className="flex items-center justify-center py-14 text-gray-500">
                  <RefreshCw size={18} className="mr-2 animate-spin" />
                  Carregando destinos...
                </div>
              ) : destinos.length === 0 ? (
                <div className="rounded-2xl border-2 border-dashed border-gray-200 bg-gray-50 px-6 py-14 text-center">
                  <MessageCircleMore className="mx-auto mb-3 text-gray-300" size={36} />
                  <p className="text-sm font-medium text-gray-700">Nenhum destino cadastrado.</p>
                  <p className="mt-1 text-sm text-gray-500">
                    Cadastre equipes, pessoas ou números de WhatsApp para o transbordo da IA.
                  </p>
                </div>
              ) : (
                <div className="grid gap-4 xl:grid-cols-2">
                  {destinos.map((destino) => (
                    <div key={destino.id} className="rounded-2xl border border-gray-200 bg-gray-50/70 p-5">
                      <div className="mb-4 flex items-start justify-between gap-4">
                        <div>
                          <h3 className="text-lg font-semibold text-gray-900">{destino.nome_destino}</h3>
                          <p className="mt-1 text-xs text-gray-500">
                            Criado em {formatDate(destino.criado_em)}
                          </p>
                        </div>
                        <div className="flex items-center gap-2">
                          <button
                            type="button"
                            onClick={() => handleTestarDestino(destino)}
                            disabled={testingId === destino.id}
                            className="inline-flex items-center gap-2 rounded-lg border border-emerald-200 bg-white px-3 py-2 text-sm text-emerald-700 transition-colors hover:bg-emerald-50 disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            {testingId === destino.id ? (
                              <RefreshCw size={14} className="animate-spin" />
                            ) : (
                              <Send size={14} />
                            )}
                            Testar Notificação
                          </button>
                          <button
                            type="button"
                            onClick={() => openEditModal(destino)}
                            className="inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-white px-3 py-2 text-sm text-blue-700 transition-colors hover:bg-blue-50"
                          >
                            <Pencil size={14} />
                            Editar
                          </button>
                          <button
                            type="button"
                            onClick={() => handleDelete(destino)}
                            disabled={deletingId === destino.id}
                            className="inline-flex items-center gap-2 rounded-lg border border-red-200 bg-white px-3 py-2 text-sm text-red-600 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            {deletingId === destino.id ? (
                              <RefreshCw size={14} className="animate-spin" />
                            ) : (
                              <Trash2 size={14} />
                            )}
                            Excluir
                          </button>
                        </div>
                      </div>

                      <div className="mb-4">
                        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
                          Números de WhatsApp de destino
                        </p>
                        <div className="flex flex-wrap gap-2">
                          {(destino.contatos_destino || []).length > 0 ? (
                            destino.contatos_destino.map((contato) => (
                              <span
                                key={contato}
                                className="rounded-full border border-emerald-200 bg-emerald-50 px-3 py-1 text-xs font-medium text-emerald-700"
                              >
                                {contato}
                              </span>
                            ))
                          ) : (
                            <span className="text-sm text-gray-400">Nenhum número informado.</span>
                          )}
                        </div>
                      </div>

                      <div>
                        <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
                          Instruções de ativação
                        </p>
                        <div className="rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm leading-6 text-gray-700">
                          {destino.instrucoes_ativacao || "Sem instruções cadastradas."}
                        </div>
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-4">
              {loadingHistorico ? (
                <div className="flex items-center justify-center py-14 text-gray-500">
                  <RefreshCw size={18} className="mr-2 animate-spin" />
                  Carregando histórico...
                </div>
              ) : emptyHistorico ? (
                <div className="rounded-2xl border-2 border-dashed border-gray-200 bg-gray-50 px-6 py-14 text-center">
                  <ClipboardList className="mx-auto mb-3 text-gray-300" size={36} />
                  <p className="text-sm font-medium text-gray-700">Nenhuma transferência auditada ainda.</p>
                  <p className="mt-1 text-sm text-gray-500">
                    Quando a IA registrar transbordos, o histórico aparecerá aqui.
                  </p>
                </div>
              ) : (
                <div className="overflow-x-auto rounded-2xl border border-gray-200">
                  <table className="min-w-full divide-y divide-gray-200 text-left text-sm">
                    <thead className="bg-gray-50 text-xs uppercase tracking-wide text-gray-500">
                      <tr>
                        <th className="px-4 py-3 font-semibold">Data</th>
                        <th className="px-4 py-3 font-semibold">Lead</th>
                        <th className="px-4 py-3 font-semibold">Destino</th>
                        <th className="px-4 py-3 font-semibold">Motivo</th>
                      </tr>
                    </thead>
                    <tbody className="divide-y divide-gray-100 bg-white">
                      {historico.map((item) => (
                        <tr key={item.id} className="align-top">
                          <td className="px-4 py-3 text-gray-600">{formatDate(item.criado_em)}</td>
                          <td className="px-4 py-3 font-medium text-gray-900">{item.lead_nome || "-"}</td>
                          <td className="px-4 py-3 text-gray-700">{item.destino_nome || "-"}</td>
                          <td className="max-w-xl px-4 py-3 text-gray-600">
                            <div className="space-y-2">
                              <p>{item.motivo_ia || "-"}</p>
                              {item.resumo_enviado ? (
                                <div className="rounded-lg bg-gray-50 p-3 text-xs text-gray-500">
                                  <span className="font-semibold text-gray-600">Resumo enviado:</span>{" "}
                                  {item.resumo_enviado}
                                </div>
                              ) : null}
                            </div>
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {showModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-lg bg-white shadow-xl">
            <div className="flex shrink-0 items-center justify-between border-b border-gray-100 p-4 md:p-6">
              <div>
                <h2 className="text-xl font-bold text-gray-900">
                  {editingDestino ? "Editar destino" : "Novo destino de transferência"}
                </h2>
                <p className="text-sm text-gray-500">
                  Cadastre os contatos que podem assumir conversas transferidas pela IA.
                </p>
              </div>
              <button
                type="button"
                onClick={closeModal}
                className="rounded-lg p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700"
              >
                <X size={18} />
              </button>
            </div>

            <form onSubmit={handleSubmit} className="flex flex-1 flex-col overflow-hidden">
              <div className="flex-1 overflow-y-auto p-4 md:p-6">
                <div className="space-y-5">
                  <div>
                    <label className="mb-1 block text-sm font-medium text-gray-700">Nome do destino</label>
                    <input
                      required
                      value={formData.nome_destino}
                      onChange={(e) => setFormData((prev) => ({ ...prev, nome_destino: e.target.value }))}
                      placeholder="Ex: SDR Comercial, Plantão Suporte, Recepção"
                      className="w-full rounded-xl border border-gray-200 px-4 py-2.5 text-gray-800 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                    />
                  </div>

                  <div>
                    <label className="mb-1 block text-sm font-medium text-gray-700">
                      Números de WhatsApp de destino
                    </label>
                    <textarea
                      required
                      rows={4}
                      value={formData.contatos_destino}
                      onChange={(e) => setFormData((prev) => ({ ...prev, contatos_destino: e.target.value }))}
                      placeholder={"Um número por linha ou separados por vírgula\n5511999999999\n5511888888888"}
                      className="w-full rounded-xl border border-gray-200 px-4 py-3 text-gray-800 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                    />
                    <p className="mt-1 text-xs text-gray-500">
                      A IA usará esta lista como possíveis contatos de transbordo.
                    </p>
                  </div>

                  <div>
                    <label className="mb-1 block text-sm font-medium text-gray-700">
                      Instruções de ativação
                    </label>
                    <textarea
                      rows={6}
                      value={formData.instrucoes_ativacao}
                      onChange={(e) => setFormData((prev) => ({ ...prev, instrucoes_ativacao: e.target.value }))}
                      placeholder="Explique quando a IA deve usar este destino, o contexto ideal e qualquer observação operacional importante."
                      className="w-full rounded-xl border border-gray-200 px-4 py-3 text-gray-800 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                </div>
              </div>
              <div className="flex shrink-0 justify-end gap-3 rounded-b-lg border-t border-gray-100 bg-gray-50 p-4 md:p-6">
                <button
                  type="button"
                  onClick={closeModal}
                  className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-50"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {saving ? "Salvando..." : editingDestino ? "Salvar alterações" : "Criar destino"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </div>
  );
}

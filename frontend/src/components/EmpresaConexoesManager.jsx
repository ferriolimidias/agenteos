import { useEffect, useState } from "react";
import {
  Check,
  Copy,
  Pencil,
  Instagram,
  MessageCircle,
  Plus,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";

import api from "../services/api";

const initialForm = {
  tipo: "evolution",
  nome_instancia: "",
  evolution_url: "",
  evolution_apikey: "",
  evolution_instance: "",
  access_token: "",
  resource_id: "",
};

function getTipoLabel(tipo) {
  if (tipo === "evolution") return "WhatsApp / Evolution";
  if (tipo === "meta") return "WhatsApp / Meta";
  if (tipo === "instagram") return "Instagram";
  return tipo;
}

function getTipoIcon(tipo) {
  if (tipo === "instagram") {
    return <Instagram size={18} className="text-pink-500" />;
  }
  return <MessageCircle size={18} className="text-green-500" />;
}

export default function EmpresaConexoesManager({ empresaId, empresaNome }) {
  const [conexoes, setConexoes] = useState([]);
  const [statusMap, setStatusMap] = useState({});
  const [loading, setLoading] = useState(false);
  const [showAddModal, setShowAddModal] = useState(false);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [error, setError] = useState(null);
  const [modalError, setModalError] = useState(null);
  const [copiedUrl, setCopiedUrl] = useState("");
  const [editingConexao, setEditingConexao] = useState(null);
  const [form, setForm] = useState(initialForm);

  const loadStatusForConexoes = async (listaConexoes) => {
    try {
      const results = await Promise.all(
        (listaConexoes || []).map(async (conexao) => {
          try {
            const res = await api.get(`/conexoes/${conexao.id}/status`);
            return [conexao.id, res.data];
          } catch (err) {
            return [
              conexao.id,
              {
                status: "DISCONNECTED",
                online: false,
              },
            ];
          }
        })
      );

      setStatusMap(Object.fromEntries(results));
    } catch (err) {
      console.error("Erro ao carregar status das conexões:", err);
    }
  };

  const fetchConexoes = async () => {
    if (!empresaId) return;

    try {
      setLoading(true);
      setError(null);
      const res = await api.get(`/empresas/${empresaId}/conexoes/`);
      const lista = res.data || [];
      setConexoes(lista);
      await loadStatusForConexoes(lista);
    } catch (err) {
      console.error("Erro ao buscar conexões:", err);
      setError(err.response?.data?.detail || "Erro ao carregar conexões.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchConexoes();
  }, [empresaId]);

  const resetForm = () => {
    setForm(initialForm);
    setModalError(null);
    setEditingConexao(null);
  };

  const openAddModal = () => {
    resetForm();
    setShowAddModal(true);
  };

  const openEditModal = (conexao) => {
    const masked = conexao.credenciais_masked || {};
    setEditingConexao(conexao);
    setModalError(null);
    setForm({
      tipo: conexao.tipo,
      nome_instancia: conexao.nome_instancia || "",
      evolution_url: masked.evolution_url || "",
      evolution_apikey: masked.evolution_apikey || "",
      evolution_instance: masked.evolution_instance || "",
      access_token: masked.access_token || "",
      resource_id: masked.resource_id || "",
    });
    setShowAddModal(true);
  };

  const closeAddModal = () => {
    setShowAddModal(false);
    resetForm();
  };

  const handleChange = (field, value) => {
    setForm((prev) => ({ ...prev, [field]: value }));
  };

  const handleCopy = async (webhookUrl) => {
    try {
      await navigator.clipboard.writeText(webhookUrl);
      setCopiedUrl(webhookUrl);
      window.setTimeout(() => setCopiedUrl(""), 2000);
    } catch (err) {
      console.error("Erro ao copiar webhook:", err);
      alert("Não foi possível copiar a URL do webhook.");
    }
  };

  const handleDelete = async (conexao) => {
    if (!window.confirm(`Excluir a conexão "${conexao.nome_instancia}"?`)) {
      return;
    }

    try {
      setDeletingId(conexao.id);
      await api.delete(`/empresas/${empresaId}/conexoes/${conexao.id}`);
      await fetchConexoes();
    } catch (err) {
      console.error("Erro ao excluir conexão:", err);
      alert(err.response?.data?.detail || "Erro ao excluir conexão.");
    } finally {
      setDeletingId(null);
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setSaving(true);
    setModalError(null);
    const isEditing = Boolean(editingConexao);
    const masked = editingConexao?.credenciais_masked || {};

    const payload =
      form.tipo === "evolution"
        ? {
            tipo: "evolution",
            nome_instancia: form.nome_instancia,
            credenciais: {
              evolution_url: form.evolution_url,
              evolution_instance: form.evolution_instance,
              ...(form.evolution_apikey && form.evolution_apikey !== masked.evolution_apikey
                ? { evolution_apikey: form.evolution_apikey }
                : {}),
            },
          }
        : {
            tipo: form.tipo,
            nome_instancia: form.nome_instancia || form.resource_id,
            credenciais: {
              resource_id: form.resource_id,
              ...(form.access_token && form.access_token !== masked.access_token
                ? { access_token: form.access_token }
                : {}),
            },
          };

    try {
      if (isEditing) {
        await api.put(`/empresas/${empresaId}/conexoes/${editingConexao.id}`, payload);
      } else {
        await api.post(`/empresas/${empresaId}/conexoes/`, payload);
      }
      await fetchConexoes();
      closeAddModal();
    } catch (err) {
      console.error("Erro ao salvar conexão:", err);
      setModalError(
        err.response?.data?.detail ||
          "Erro ao salvar conexão. Verifique as credenciais e tente novamente."
      );
    } finally {
      setSaving(false);
    }
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-4">
        <div>
          <h3 className="text-lg font-semibold text-gray-800">Canais / Conexões</h3>
          <p className="text-sm text-gray-500">
            Gerencie os canais de entrada e a URL de webhook de cada conexão da empresa.
          </p>
        </div>

        <button
          type="button"
          onClick={openAddModal}
          className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-700"
        >
          <Plus size={16} />
          Adicionar Conexão
        </button>
      </div>

      <div className="rounded-xl border border-gray-200 bg-gray-50 p-4">
        <div className="mb-4 flex items-center justify-between">
          <p className="text-sm text-gray-500">
            Empresa: <span className="font-medium text-gray-700">{empresaNome}</span>
          </p>
          <button
            type="button"
            onClick={fetchConexoes}
            className="inline-flex items-center gap-2 rounded-lg border border-gray-200 bg-white px-3 py-2 text-sm text-gray-600 transition-colors hover:bg-gray-100"
          >
            <RefreshCw size={14} />
            Atualizar
          </button>
        </div>

        {loading ? (
          <div className="flex items-center justify-center py-10 text-gray-500">
            <RefreshCw size={18} className="mr-2 animate-spin" />
            Carregando conexões...
          </div>
        ) : error ? (
          <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
            {error}
          </div>
        ) : conexoes.length === 0 ? (
          <div className="rounded-lg border border-dashed border-gray-300 bg-white px-4 py-8 text-center text-sm text-gray-500">
            Nenhuma conexão cadastrada para esta empresa.
          </div>
        ) : (
          <div className="space-y-3">
            {conexoes.map((conexao) => (
              <div
                key={conexao.id}
                className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm"
              >
                {(() => {
                  const statusInfo = statusMap[conexao.id] || {};
                  const isOnline = Boolean(statusInfo.online);
                  return (
                <div className="mb-3 flex items-start justify-between gap-4">
                  <div className="flex items-start gap-3">
                    <div className="mt-0.5 rounded-lg bg-gray-100 p-2">
                      {getTipoIcon(conexao.tipo)}
                    </div>
                    <div>
                      <div className="flex items-center gap-2">
                        <h4 className="font-semibold text-gray-800">
                          {getTipoLabel(conexao.tipo)}
                        </h4>
                        <span className="rounded-full bg-emerald-100 px-2.5 py-0.5 text-xs font-medium text-emerald-700">
                          {conexao.status}
                        </span>
                        <span className="inline-flex items-center gap-1.5 rounded-full bg-gray-100 px-2.5 py-0.5 text-xs font-medium text-gray-700">
                          <span
                            className={`h-2.5 w-2.5 rounded-full ${
                              isOnline ? "bg-emerald-500" : "bg-red-500"
                            }`}
                          />
                          {statusInfo.status || "Verificando"}
                        </span>
                      </div>
                      <p className="mt-1 text-sm text-gray-600">
                        Nome da Instância:{" "}
                        <span className="font-medium text-gray-800">{conexao.nome_instancia}</span>
                      </p>
                    </div>
                  </div>
                </div>
                  );
                })()}

                <div className="mb-3 flex items-center justify-end gap-2">
                  <button
                    type="button"
                    onClick={() => openEditModal(conexao)}
                    className="inline-flex items-center gap-2 rounded-lg border border-indigo-200 px-3 py-2 text-sm text-indigo-600 transition-colors hover:bg-indigo-50"
                  >
                    <Pencil size={14} />
                    Editar
                  </button>
                  <button
                    type="button"
                    onClick={() => handleDelete(conexao)}
                    disabled={deletingId === conexao.id}
                    className="inline-flex items-center gap-2 rounded-lg border border-red-200 px-3 py-2 text-sm text-red-600 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {deletingId === conexao.id ? (
                      <RefreshCw size={14} className="animate-spin" />
                    ) : (
                      <Trash2 size={14} />
                    )}
                    Excluir
                  </button>
                </div>

                <div className="space-y-2">
                  <p className="text-xs font-semibold uppercase tracking-wide text-gray-500">
                    URL de Webhook
                  </p>
                  <div className="flex flex-col gap-2 md:flex-row">
                    <input
                      readOnly
                      value={conexao.webhook_url}
                      className="w-full rounded-lg border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700 outline-none"
                    />
                    <button
                      type="button"
                      onClick={() => handleCopy(conexao.webhook_url)}
                      className={`inline-flex shrink-0 items-center justify-center gap-2 rounded-lg px-4 py-2 text-sm font-medium transition-colors ${
                        copiedUrl === conexao.webhook_url
                          ? "border border-emerald-200 bg-emerald-50 text-emerald-700"
                          : "border border-gray-200 bg-white text-gray-700 hover:bg-gray-100"
                      }`}
                    >
                      {copiedUrl === conexao.webhook_url ? <Check size={14} /> : <Copy size={14} />}
                      {copiedUrl === conexao.webhook_url ? "URL Copiada" : "Copiar URL"}
                    </button>
                  </div>
                  <p className="text-xs text-gray-500">
                    Configure este webhook no provedor para receber mensagens inbound.
                  </p>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      {showAddModal && (
        <div className="fixed inset-0 z-[60] flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-2xl rounded-2xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
              <div>
                <h3 className="text-xl font-bold text-gray-800">
                  {editingConexao ? "Editar Conexão" : "Adicionar Conexão"}
                </h3>
                <p className="text-sm text-gray-500">
                  O sistema testa a conectividade antes de salvar.
                </p>
              </div>
              <button
                type="button"
                onClick={closeAddModal}
                className="rounded-lg p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700"
              >
                <X size={18} />
              </button>
            </div>

            <form onSubmit={handleSubmit} className="space-y-5 p-6">
              {modalError && (
                <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                  {modalError}
                </div>
              )}

              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Tipo</label>
                <select
                  value={form.tipo}
                  onChange={(e) => setForm({ ...initialForm, tipo: e.target.value })}
                  disabled={Boolean(editingConexao)}
                  className="w-full rounded-lg border border-gray-200 px-3 py-2 text-gray-800 outline-none transition-all focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                >
                  <option value="evolution">Evolution</option>
                  <option value="meta">Meta</option>
                  <option value="instagram">Instagram</option>
                </select>
              </div>

              {form.tipo === "evolution" ? (
                <>
                  <div>
                    <label className="mb-1 block text-sm font-medium text-gray-700">
                      URL da API
                    </label>
                    <input
                      type="url"
                      required
                      value={form.evolution_url}
                      onChange={(e) => handleChange("evolution_url", e.target.value)}
                      placeholder="https://api.seudominio.com"
                      className="w-full rounded-lg border border-gray-200 px-3 py-2 text-gray-800 outline-none transition-all focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-sm font-medium text-gray-700">
                      API Key
                    </label>
                    <input
                      type="password"
                      required
                      value={form.evolution_apikey}
                      onChange={(e) => handleChange("evolution_apikey", e.target.value)}
                      placeholder={editingConexao ? "************abcd" : "apikey da Evolution"}
                      className="w-full rounded-lg border border-gray-200 px-3 py-2 text-gray-800 outline-none transition-all focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                    />
                  </div>
                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <label className="mb-1 block text-sm font-medium text-gray-700">
                        Nome da Instância
                      </label>
                      <input
                        type="text"
                        required
                        value={form.nome_instancia}
                        onChange={(e) => handleChange("nome_instancia", e.target.value)}
                        placeholder="whatsapp-cliente-01"
                        className="w-full rounded-lg border border-gray-200 px-3 py-2 text-gray-800 outline-none transition-all focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                      />
                    </div>
                    <div>
                      <label className="mb-1 block text-sm font-medium text-gray-700">
                        Nome da Instância na API
                      </label>
                      <input
                        type="text"
                        required
                        value={form.evolution_instance}
                        onChange={(e) => handleChange("evolution_instance", e.target.value)}
                        placeholder="instancia configurada na Evolution"
                        className="w-full rounded-lg border border-gray-200 px-3 py-2 text-gray-800 outline-none transition-all focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                      />
                    </div>
                  </div>
                </>
              ) : (
                <>
                  <div>
                    <label className="mb-1 block text-sm font-medium text-gray-700">
                      Token de Acesso
                    </label>
                    <input
                      type="password"
                      required
                      value={form.access_token}
                      onChange={(e) => handleChange("access_token", e.target.value)}
                      placeholder={editingConexao ? "************abcd" : "Token do Meta / Instagram"}
                      className="w-full rounded-lg border border-gray-200 px-3 py-2 text-gray-800 outline-none transition-all focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-sm font-medium text-gray-700">
                      ID do Telefone / Página
                    </label>
                    <input
                      type="text"
                      required
                      value={form.resource_id}
                      onChange={(e) => handleChange("resource_id", e.target.value)}
                      placeholder="Ex: 123456789012345"
                      className="w-full rounded-lg border border-gray-200 px-3 py-2 text-gray-800 outline-none transition-all focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                    />
                  </div>
                  <div>
                    <label className="mb-1 block text-sm font-medium text-gray-700">
                      Nome da Instância
                    </label>
                    <input
                      type="text"
                      required
                      value={form.nome_instancia}
                      onChange={(e) => handleChange("nome_instancia", e.target.value)}
                      placeholder="Nome amigável da conexão"
                      className="w-full rounded-lg border border-gray-200 px-3 py-2 text-gray-800 outline-none transition-all focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                    />
                  </div>
                </>
              )}

              <div className="flex justify-end gap-3 pt-2">
                <button
                  type="button"
                  onClick={closeAddModal}
                  className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-100"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-70"
                >
                  {saving ? (
                    <>
                      <RefreshCw size={14} className="animate-spin" />
                      Testando e salvando...
                    </>
                  ) : (
                    editingConexao ? "Salvar Alterações" : "Salvar Conexão"
                  )}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}

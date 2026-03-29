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

export default function EmpresaConexoesManager({
  empresaId,
  empresaNome,
  conexaoDisparoId = null,
  disparoDelayMin = 3,
  disparoDelayMax = 7,
  onConexaoDisparoUpdated,
}) {
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
  const [selectedConexaoDisparoId, setSelectedConexaoDisparoId] = useState(conexaoDisparoId || "");
  const [selectedDisparoDelayMin, setSelectedDisparoDelayMin] = useState(disparoDelayMin ?? 3);
  const [selectedDisparoDelayMax, setSelectedDisparoDelayMax] = useState(disparoDelayMax ?? 7);
  const [savingConexaoDisparo, setSavingConexaoDisparo] = useState(false);
  const [qrModalConexao, setQrModalConexao] = useState(null);
  const [qrCodeBase64, setQrCodeBase64] = useState("");
  const [loadingQrCode, setLoadingQrCode] = useState(false);
  const [qrCodeError, setQrCodeError] = useState("");
  const [checkingQrStatus, setCheckingQrStatus] = useState(false);
  const [toast, setToast] = useState(null);

  useEffect(() => {
    setSelectedConexaoDisparoId(conexaoDisparoId || "");
  }, [conexaoDisparoId]);

  useEffect(() => {
    setSelectedDisparoDelayMin(disparoDelayMin ?? 3);
  }, [disparoDelayMin]);

  useEffect(() => {
    setSelectedDisparoDelayMax(disparoDelayMax ?? 7);
  }, [disparoDelayMax]);

  useEffect(() => {
    if (!toast) return undefined;
    const timer = window.setTimeout(() => setToast(null), 4000);
    return () => window.clearTimeout(timer);
  }, [toast]);

  const loadStatusForConexoes = async (listaConexoes) => {
    try {
      const results = await Promise.all(
        (listaConexoes || []).map(async (conexao) => {
          try {
            const res = await api.get(`/conexoes/status/${conexao.id}`);
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

  useEffect(() => {
    if (!qrModalConexao?.id) return undefined;

    const intervalId = window.setInterval(async () => {
      try {
        const res = await api.get(`/conexoes/status/${qrModalConexao.id}`);
        setStatusMap((prev) => ({ ...prev, [qrModalConexao.id]: res.data }));
        const status = String(res.data?.status || "").toLowerCase();
        if (status === "open" || res.data?.online) {
          setQrModalConexao(null);
          setQrCodeBase64("");
          setQrCodeError("");
          setToast({
            type: "success",
            message: `Conexão "${qrModalConexao.nome_instancia}" conectada com sucesso.`,
          });
          await fetchConexoes();
        }
      } catch (err) {
        console.error("Erro no polling de status da conexão:", err);
      }
    }, 5000);

    return () => window.clearInterval(intervalId);
  }, [qrModalConexao]);

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
            nome_instancia: form.nome_instancia || form.evolution_instance,
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

  const handleSaveConexaoDisparo = async () => {
    try {
      setSavingConexaoDisparo(true);
      const delayMin = Number(selectedDisparoDelayMin);
      const delayMax = Number(selectedDisparoDelayMax);

      if (Number.isNaN(delayMin) || Number.isNaN(delayMax)) {
        alert("Informe valores válidos para o intervalo de disparo.");
        return;
      }
      if (delayMin < 0 || delayMax < 0) {
        alert("Os intervalos de disparo não podem ser negativos.");
        return;
      }
      if (delayMin > delayMax) {
        alert("O intervalo mínimo de disparo não pode ser maior que o máximo.");
        return;
      }

      await api.put(`/empresas/${empresaId}`, {
        conexao_disparo_id: selectedConexaoDisparoId || "",
        disparo_delay_min: delayMin,
        disparo_delay_max: delayMax,
      });
      onConexaoDisparoUpdated?.({
        conexao_disparo_id: selectedConexaoDisparoId || null,
        disparo_delay_min: delayMin,
        disparo_delay_max: delayMax,
      });
    } catch (err) {
      console.error("Erro ao salvar conexão padrão de disparo:", err);
      alert(err.response?.data?.detail || "Erro ao salvar conexão padrão para disparos.");
    } finally {
      setSavingConexaoDisparo(false);
    }
  };

  const closeQrModal = () => {
    setQrModalConexao(null);
    setQrCodeBase64("");
    setQrCodeError("");
    setLoadingQrCode(false);
    setCheckingQrStatus(false);
  };

  const handleOpenQrModal = async (conexao) => {
    const cred = conexao?.credenciais_masked || {};
    const evolutionUrl = String(cred?.evolution_url || "").trim();
    const evolutionApiKey = String(cred?.evolution_apikey || "").trim();
    if (!evolutionUrl || !evolutionApiKey) {
      setToast({
        type: "error",
        message: "Configure URL da Evolution e API Key antes de gerar o QR Code.",
      });
      return;
    }

    setQrModalConexao(conexao);
    setQrCodeBase64("");
    setQrCodeError("");
    setLoadingQrCode(true);

    try {
      const res = await api.get(`/conexoes/${conexao.id}/qrcode`);
      setQrCodeBase64(res.data?.base64 || "");
    } catch (err) {
      console.error("Erro ao obter QR Code:", err);
      const detail = err.response?.data?.detail || "Não foi possível gerar o QR Code desta conexão.";
      setQrCodeError(detail);
      if (err.response?.status === 409) {
        setToast({
          type: "success",
          message: detail,
        });
        closeQrModal();
        await fetchConexoes();
      }
    } finally {
      setLoadingQrCode(false);
    }
  };

  const handleRefreshQrStatus = async () => {
    if (!qrModalConexao?.id) return;
    try {
      setCheckingQrStatus(true);
      const res = await api.get(`/conexoes/status/${qrModalConexao.id}`);
      setStatusMap((prev) => ({ ...prev, [qrModalConexao.id]: res.data }));
      const status = String(res.data?.status || "").toLowerCase();
      if (status === "open" || res.data?.online) {
        setToast({
          type: "success",
          message: `Conexão "${qrModalConexao.nome_instancia}" conectada com sucesso.`,
        });
        closeQrModal();
        await fetchConexoes();
      }
    } catch (err) {
      console.error("Erro ao atualizar status da conexão:", err);
      setQrCodeError(err.response?.data?.detail || "Não foi possível atualizar o status da conexão.");
    } finally {
      setCheckingQrStatus(false);
    }
  };

  return (
    <div className="space-y-4">
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
                {toast.type === "success" ? <Check size={14} /> : <X size={14} />}
              </div>
              <div className="flex-1">
                <p className="text-sm font-semibold">
                  {toast.type === "success" ? "Tudo certo" : "Atenção"}
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

        <div className="mb-4 rounded-xl border border-indigo-100 bg-indigo-50/70 p-4">
          <div className="flex flex-col gap-3 lg:flex-row lg:items-end lg:justify-between">
            <div className="flex-1 space-y-4">
              <div>
                <label className="mb-1 block text-sm font-semibold text-gray-800">
                  Conexão Padrão para Disparos
                </label>
                <select
                  value={selectedConexaoDisparoId}
                  onChange={(e) => setSelectedConexaoDisparoId(e.target.value)}
                  className="w-full rounded-xl border border-indigo-200 bg-white px-3 py-2 text-sm text-gray-800 outline-none transition-all focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                >
                  <option value="">Nenhuma conexão padrão selecionada</option>
                  {conexoes.map((conexao) => (
                    <option key={conexao.id} value={conexao.id}>
                      {getTipoLabel(conexao.tipo)} - {conexao.nome_instancia}
                    </option>
                  ))}
                </select>
                <p className="mt-1 text-xs text-gray-500">
                  Essa conexão será usada como canal padrão para disparos em massa da empresa.
                </p>
              </div>

              <div className="grid gap-3 md:grid-cols-2">
                <div>
                  <label className="mb-1 block text-sm font-semibold text-gray-800">
                    Intervalo Mín. de Disparo (seg)
                  </label>
                  <input
                    type="number"
                    min="0"
                    value={selectedDisparoDelayMin}
                    onChange={(e) => setSelectedDisparoDelayMin(e.target.value)}
                    className="w-full rounded-xl border border-indigo-200 bg-white px-3 py-2 text-sm text-gray-800 outline-none transition-all focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                  />
                </div>
                <div>
                  <label className="mb-1 block text-sm font-semibold text-gray-800">
                    Intervalo Máx. de Disparo (seg)
                  </label>
                  <input
                    type="number"
                    min="0"
                    value={selectedDisparoDelayMax}
                    onChange={(e) => setSelectedDisparoDelayMax(e.target.value)}
                    className="w-full rounded-xl border border-indigo-200 bg-white px-3 py-2 text-sm text-gray-800 outline-none transition-all focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                  />
                </div>
              </div>
              <p className="text-xs text-gray-500">
                O disparo em massa aguardará um tempo aleatório entre esses dois valores para reduzir risco de bloqueio.
              </p>
            </div>

            <button
              type="button"
              onClick={handleSaveConexaoDisparo}
              disabled={savingConexaoDisparo}
              className="inline-flex items-center justify-center gap-2 rounded-xl bg-indigo-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-indigo-700 disabled:cursor-not-allowed disabled:opacity-60"
            >
              {savingConexaoDisparo ? (
                <>
                  <RefreshCw size={14} className="animate-spin" />
                  Salvando...
                </>
              ) : (
                "Salvar conexão padrão"
              )}
            </button>
          </div>
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
                  const isDisconnectedEvolution =
                    conexao.tipo === "evolution" &&
                    (!isOnline || String(statusInfo.status || "").toUpperCase() === "DISCONNECTED");
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
                  {isDisconnectedEvolution ? (
                    <button
                      type="button"
                      onClick={() => handleOpenQrModal(conexao)}
                      className="inline-flex items-center gap-2 rounded-xl bg-emerald-600 px-4 py-2.5 text-sm font-semibold text-white transition-colors hover:bg-emerald-700"
                    >
                      <MessageCircle size={16} />
                      Conectar (QR Code)
                    </button>
                  ) : null}
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
                        Nome Amigável (Opcional)
                      </label>
                      <input
                        type="text"
                        value={form.nome_instancia}
                        onChange={(e) => handleChange("nome_instancia", e.target.value)}
                        placeholder="Se vazio, será igual ao Nome da Instância na API"
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

      {qrModalConexao ? (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-lg rounded-2xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
              <div>
                <h3 className="text-xl font-bold text-gray-800">Conectar WhatsApp</h3>
                <p className="text-sm text-gray-500">
                  Escaneie o QR Code com o celular para conectar a instância `{qrModalConexao.nome_instancia}`.
                </p>
              </div>
              <button
                type="button"
                onClick={closeQrModal}
                className="rounded-lg p-2 text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-700"
              >
                <X size={18} />
              </button>
            </div>

            <div className="space-y-5 p-6">
              <div className="rounded-xl border border-emerald-100 bg-emerald-50 px-4 py-3 text-sm text-emerald-800">
                O status será verificado automaticamente a cada 5 segundos. Quando a conexão ficar ativa, esta janela será fechada.
              </div>

              {loadingQrCode ? (
                <div className="flex flex-col items-center justify-center py-12 text-gray-500">
                  <RefreshCw size={22} className="mb-3 animate-spin" />
                  Gerando QR Code...
                </div>
              ) : qrCodeError ? (
                <div className="rounded-xl border border-red-200 bg-red-50 px-4 py-4 text-sm text-red-700">
                  {qrCodeError}
                </div>
              ) : qrCodeBase64 ? (
                <div className="flex flex-col items-center rounded-2xl border border-gray-200 bg-gray-50 p-6">
                  <img
                    src={`data:image/png;base64,${qrCodeBase64}`}
                    alt="QR Code para conectar WhatsApp"
                    className="h-72 w-72 rounded-xl border border-gray-200 bg-white p-3 shadow-sm"
                  />
                  <p className="mt-4 text-center text-sm text-gray-500">
                    Abra o WhatsApp no celular, vá em aparelhos conectados e escaneie o código acima.
                  </p>
                </div>
              ) : (
                <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-4 text-sm text-amber-800">
                  A Evolution não retornou um QR Code neste momento. Tente novamente em instantes.
                </div>
              )}

              <div className="flex justify-end gap-3">
                <button
                  type="button"
                  onClick={closeQrModal}
                  className="rounded-lg border border-gray-200 px-4 py-2 text-sm font-medium text-gray-600 transition-colors hover:bg-gray-100"
                >
                  Fechar
                </button>
                <button
                  type="button"
                  onClick={handleRefreshQrStatus}
                  disabled={checkingQrStatus}
                  className="inline-flex items-center gap-2 rounded-lg bg-emerald-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-emerald-700 disabled:cursor-not-allowed disabled:opacity-70"
                >
                  {checkingQrStatus ? (
                    <>
                      <RefreshCw size={14} className="animate-spin" />
                      Verificando...
                    </>
                  ) : (
                    <>
                      <RefreshCw size={14} />
                      Atualizar Status
                    </>
                  )}
                </button>
              </div>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

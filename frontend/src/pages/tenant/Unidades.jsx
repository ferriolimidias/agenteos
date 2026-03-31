import { useEffect, useMemo, useState } from "react";
import {
  Building2,
  MapPin,
  Pencil,
  Plus,
  RefreshCw,
  Trash2,
  X,
} from "lucide-react";

import api from "../../services/api";
import { getActiveEmpresaId } from "../../utils/auth";

const initialForm = {
  nome_unidade: "",
  endereco_completo: "",
  link_google_maps: "",
  horario_funcionamento: "",
  is_matriz: false,
};

export default function Unidades() {
  const empresaId = getActiveEmpresaId();

  const [unidades, setUnidades] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [showModal, setShowModal] = useState(false);
  const [editingUnidade, setEditingUnidade] = useState(null);
  const [formData, setFormData] = useState(initialForm);
  const unidadesOrdenadas = useMemo(() => {
    return [...unidades].sort((a, b) => Number(Boolean(b.is_matriz)) - Number(Boolean(a.is_matriz)));
  }, [unidades]);

  const fetchUnidades = async () => {
    if (!empresaId) {
      setLoading(false);
      setUnidades([]);
      return;
    }
    try {
      setLoading(true);
      const res = await api.get(`/empresas/${empresaId}/unidades`);
      setUnidades(res.data || []);
    } catch (err) {
      console.error("Erro ao carregar unidades:", err);
      setUnidades([]);
      alert(err.response?.data?.detail || "Não foi possível carregar as unidades.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchUnidades();
  }, [empresaId]);

  const closeModal = () => {
    setShowModal(false);
    setEditingUnidade(null);
    setFormData(initialForm);
  };

  const openCreateModal = () => {
    setEditingUnidade(null);
    setFormData(initialForm);
    setShowModal(true);
  };

  const openEditModal = (unidade) => {
    setEditingUnidade(unidade);
    setFormData({
      nome_unidade: unidade.nome_unidade || "",
      endereco_completo: unidade.endereco_completo || "",
      link_google_maps: unidade.link_google_maps || "",
      horario_funcionamento: unidade.horario_funcionamento || "",
      is_matriz: Boolean(unidade.is_matriz),
    });
    setShowModal(true);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!empresaId) return;
    const linkGoogleMaps = formData.link_google_maps.trim();
    if (linkGoogleMaps && !/^https?:\/\//i.test(linkGoogleMaps)) {
      alert("O link do Google Maps deve começar com http:// ou https://");
      return;
    }
    try {
      setSaving(true);
      const payload = {
        ...formData,
        nome_unidade: formData.nome_unidade.trim(),
        endereco_completo: formData.endereco_completo.trim(),
        link_google_maps: linkGoogleMaps || null,
        horario_funcionamento: formData.horario_funcionamento.trim() || null,
        is_matriz: Boolean(formData.is_matriz),
      };

      if (editingUnidade) {
        await api.put(`/empresas/${empresaId}/unidades/${editingUnidade.id}`, payload);
      } else {
        await api.post(`/empresas/${empresaId}/unidades`, payload);
      }

      await fetchUnidades();
      closeModal();
    } catch (err) {
      console.error("Erro ao salvar unidade:", err);
      alert(err.response?.data?.detail || "Não foi possível salvar a unidade.");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (unidade) => {
    if (!empresaId) return;
    if (!window.confirm(`Excluir a unidade "${unidade.nome_unidade}"?`)) return;
    try {
      setDeletingId(unidade.id);
      await api.delete(`/empresas/${empresaId}/unidades/${unidade.id}`);
      await fetchUnidades();
    } catch (err) {
      console.error("Erro ao excluir unidade:", err);
      alert(err.response?.data?.detail || "Não foi possível excluir a unidade.");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="flex items-center gap-3 text-3xl font-bold tracking-tight text-gray-900">
            <Building2 className="text-blue-600" size={30} />
            Endereços / Filiais
          </h1>
          <p className="mt-1 text-gray-500">
            Gerencie matriz e filiais para o Especialista de Localização responder com dados atualizados.
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={fetchUnidades}
            className="inline-flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
          >
            <RefreshCw size={16} />
            Atualizar
          </button>
          <button
            type="button"
            onClick={openCreateModal}
            className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700"
          >
            <Plus size={16} />
            Nova Unidade
          </button>
        </div>
      </div>

      <div className="rounded-2xl border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-100 px-6 py-5">
          <h2 className="text-lg font-semibold text-gray-900">Unidades Cadastradas</h2>
          <p className="mt-1 text-sm text-gray-500">
            Mantenha endereços, horários e links de mapa para respostas mais precisas no atendimento.
          </p>
        </div>

        <div className="p-6">
          {loading ? (
            <div className="flex items-center justify-center py-14 text-gray-500">
              <RefreshCw size={18} className="mr-2 animate-spin" />
              Carregando unidades...
            </div>
          ) : unidades.length === 0 ? (
            <div className="rounded-2xl border-2 border-dashed border-gray-200 bg-gray-50 px-6 py-14 text-center">
              <MapPin className="mx-auto mb-3 text-gray-300" size={36} />
              <p className="text-sm font-medium text-gray-700">Nenhuma unidade cadastrada.</p>
              <p className="mt-1 text-sm text-gray-500">Cadastre a matriz e as filiais para habilitar respostas de localização.</p>
            </div>
          ) : (
            <div className="grid gap-4 md:grid-cols-2">
              {unidadesOrdenadas.map((unidade) => (
                <div key={unidade.id} className="rounded-2xl border border-gray-200 bg-gray-50/70 p-5">
                  <div className="mb-3 flex items-start justify-between gap-4">
                    <div>
                      <h3 className="text-lg font-semibold text-gray-900">
                        {unidade.nome_unidade}{" "}
                        {unidade.is_matriz ? (
                          <span className="ml-2 inline-flex rounded-full border border-blue-200 bg-blue-100 px-2.5 py-1 text-xs font-semibold text-blue-800">
                            Matriz
                          </span>
                        ) : null}
                      </h3>
                      <p className="mt-1 text-sm text-gray-600">{unidade.horario_funcionamento || "Horário não informado"}</p>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => openEditModal(unidade)}
                        className="inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-white px-3 py-2 text-sm text-blue-700 transition-colors hover:bg-blue-50"
                      >
                        <Pencil size={14} />
                        Editar
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDelete(unidade)}
                        disabled={deletingId === unidade.id}
                        className="inline-flex items-center gap-2 rounded-lg border border-red-200 bg-white px-3 py-2 text-sm text-red-600 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {deletingId === unidade.id ? <RefreshCw size={14} className="animate-spin" /> : <Trash2 size={14} />}
                        Excluir
                      </button>
                    </div>
                  </div>

                  <p className="mb-3 rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm text-gray-700">
                    {unidade.endereco_completo}
                  </p>

                  {unidade.link_google_maps ? (
                    <a
                      href={unidade.link_google_maps}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-2 text-sm font-medium text-blue-700 hover:text-blue-900"
                    >
                      <MapPin size={14} />
                      Abrir no Google Maps
                    </a>
                  ) : (
                    <p className="text-sm text-gray-400">Sem link do Google Maps.</p>
                  )}
                </div>
              ))}
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
                  {editingUnidade ? "Editar Unidade" : "Nova Unidade"}
                </h2>
                <p className="text-sm text-gray-500">
                  Preencha os dados de localização usados no atendimento automático.
                </p>
              </div>
              <button
                type="button"
                onClick={closeModal}
                className="rounded-lg p-2 text-gray-400 hover:bg-gray-100 hover:text-gray-700"
              >
                <X size={18} />
              </button>
            </div>

            <form onSubmit={handleSubmit} className="flex flex-1 flex-col overflow-hidden">
              <div className="flex-1 overflow-y-auto p-4 md:p-6">
                <div className="space-y-5">
                  <div>
                    <label className="mb-1 block text-sm font-medium text-gray-700">Nome da unidade</label>
                    <input
                      required
                      value={formData.nome_unidade}
                      onChange={(e) => setFormData((prev) => ({ ...prev, nome_unidade: e.target.value }))}
                      className="w-full rounded-xl border border-gray-200 px-4 py-2.5 text-gray-800 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                      placeholder="Ex: Matriz, Filial Centro"
                    />
                  </div>

                  <div>
                    <label className="mb-1 block text-sm font-medium text-gray-700">Endereço completo</label>
                    <textarea
                      required
                      rows={4}
                      value={formData.endereco_completo}
                      onChange={(e) => setFormData((prev) => ({ ...prev, endereco_completo: e.target.value }))}
                      className="w-full rounded-xl border border-gray-200 px-4 py-3 text-gray-800 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                      placeholder="Rua, número, bairro, cidade, estado, CEP..."
                    />
                  </div>

                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <label className="mb-1 block text-sm font-medium text-gray-700">Link Google Maps</label>
                      <input
                        value={formData.link_google_maps}
                        onChange={(e) => setFormData((prev) => ({ ...prev, link_google_maps: e.target.value }))}
                        className="w-full rounded-xl border border-gray-200 px-4 py-2.5 text-gray-800 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                        placeholder="https://maps.google.com/..."
                      />
                    </div>
                    <div>
                      <label className="mb-1 block text-sm font-medium text-gray-700">Horário de funcionamento</label>
                      <input
                        value={formData.horario_funcionamento}
                        onChange={(e) => setFormData((prev) => ({ ...prev, horario_funcionamento: e.target.value }))}
                        className="w-full rounded-xl border border-gray-200 px-4 py-2.5 text-gray-800 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                        placeholder="Seg a Sex: 08h às 18h"
                      />
                    </div>
                  </div>

                  <label className="inline-flex items-center gap-3 rounded-xl border border-gray-200 bg-gray-50 px-4 py-3 text-sm text-gray-700">
                    <input
                      type="checkbox"
                      checked={formData.is_matriz}
                      onChange={(e) => setFormData((prev) => ({ ...prev, is_matriz: e.target.checked }))}
                      className="h-4 w-4 rounded border-gray-300 text-blue-600 focus:ring-blue-500"
                    />
                    Definir esta unidade como Matriz
                  </label>
                </div>
              </div>

              <div className="flex shrink-0 justify-end gap-3 rounded-b-lg border-t border-gray-100 bg-gray-50 p-4 md:p-6">
                <button
                  type="button"
                  onClick={closeModal}
                  className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm font-medium text-gray-600 hover:bg-gray-50"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {saving ? "Salvando..." : editingUnidade ? "Salvar alterações" : "Criar unidade"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </div>
  );
}

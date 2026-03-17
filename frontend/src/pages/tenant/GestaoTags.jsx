import { useEffect, useState } from "react";
import { Palette, Pencil, Plus, RefreshCw, Tag, Trash2, X } from "lucide-react";

import api from "../../services/api";
import { getActiveEmpresaId } from "../../utils/auth";

const initialForm = {
  nome: "",
  cor: "#2563eb",
  instrucao_ia: "",
};

export default function GestaoTags() {
  const empresaId = getActiveEmpresaId();
  const [tags, setTags] = useState([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [showModal, setShowModal] = useState(false);
  const [editingTag, setEditingTag] = useState(null);
  const [formData, setFormData] = useState(initialForm);

  const fetchTags = async () => {
    if (!empresaId) return;
    try {
      setLoading(true);
      const res = await api.get(`/empresas/${empresaId}/crm/tags/oficiais`);
      setTags(res.data || []);
    } catch (err) {
      console.error("Erro ao carregar tags oficiais:", err);
      setTags([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchTags();
  }, [empresaId]);

  const closeModal = () => {
    setShowModal(false);
    setEditingTag(null);
    setFormData(initialForm);
  };

  const openCreateModal = () => {
    setEditingTag(null);
    setFormData(initialForm);
    setShowModal(true);
  };

  const openEditModal = (tag) => {
    setEditingTag(tag);
    setFormData({
      nome: tag.nome || "",
      cor: tag.cor || "#2563eb",
      instrucao_ia: tag.instrucao_ia || "",
    });
    setShowModal(true);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      setSaving(true);
      if (editingTag) {
        await api.put(`/empresas/${empresaId}/crm/tags/oficiais/${editingTag.id}`, formData);
      } else {
        await api.post(`/empresas/${empresaId}/crm/tags/oficiais`, formData);
      }
      await fetchTags();
      closeModal();
    } catch (err) {
      console.error("Erro ao salvar tag:", err);
      alert(err.response?.data?.detail || "Não foi possível salvar a tag.");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (tag) => {
    if (!window.confirm(`Excluir a tag "${tag.nome}"?`)) return;
    try {
      setDeletingId(tag.id);
      await api.delete(`/empresas/${empresaId}/crm/tags/oficiais/${tag.id}`);
      await fetchTags();
    } catch (err) {
      console.error("Erro ao excluir tag:", err);
      alert(err.response?.data?.detail || "Não foi possível excluir a tag.");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="flex items-center gap-3 text-3xl font-bold tracking-tight text-gray-900">
            <Tag className="text-blue-600" size={30} />
            Gestão de Tags
          </h1>
          <p className="mt-1 text-gray-500">
            Cadastre as tags oficiais do CRM, suas cores e as instruções que a IA deve seguir para classificar leads.
          </p>
        </div>

        <div className="flex gap-2">
          <button
            type="button"
            onClick={fetchTags}
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
            Nova Tag
          </button>
        </div>
      </div>

      <div className="rounded-2xl border border-gray-200 bg-white shadow-sm">
        <div className="p-6">
          {loading ? (
            <div className="flex items-center justify-center py-14 text-gray-500">
              <RefreshCw size={18} className="mr-2 animate-spin" />
              Carregando tags oficiais...
            </div>
          ) : tags.length === 0 ? (
            <div className="rounded-2xl border-2 border-dashed border-gray-200 bg-gray-50 px-6 py-14 text-center">
              <Tag className="mx-auto mb-3 text-gray-300" size={36} />
              <p className="text-sm font-medium text-gray-700">Nenhuma tag oficial cadastrada.</p>
              <p className="mt-1 text-sm text-gray-500">
                Crie audiências como VIP, Reengajar, Urgente ou Qualificado para organizar CRM e campanhas.
              </p>
            </div>
          ) : (
            <div className="grid gap-4 xl:grid-cols-2">
              {tags.map((tag) => (
                <div key={tag.id} className="rounded-2xl border border-gray-200 bg-gray-50/70 p-5">
                  <div className="mb-4 flex items-start justify-between gap-4">
                    <div>
                      <span
                        className="inline-flex rounded-full border px-3 py-1 text-xs font-semibold"
                        style={{ backgroundColor: `${tag.cor}18`, borderColor: `${tag.cor}55`, color: tag.cor }}
                      >
                        {tag.nome}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        type="button"
                        onClick={() => openEditModal(tag)}
                        className="inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-white px-3 py-2 text-sm text-blue-700 transition-colors hover:bg-blue-50"
                      >
                        <Pencil size={14} />
                        Editar
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDelete(tag)}
                        disabled={deletingId === tag.id}
                        className="inline-flex items-center gap-2 rounded-lg border border-red-200 bg-white px-3 py-2 text-sm text-red-600 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60"
                      >
                        {deletingId === tag.id ? <RefreshCw size={14} className="animate-spin" /> : <Trash2 size={14} />}
                        Excluir
                      </button>
                    </div>
                  </div>

                  <div className="mb-4 flex items-center gap-3 rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm text-gray-700">
                    <Palette size={16} style={{ color: tag.cor }} />
                    <span className="font-medium">{tag.cor}</span>
                  </div>

                  <div className="rounded-xl border border-gray-200 bg-white px-4 py-3 text-sm leading-6 text-gray-700">
                    {tag.instrucao_ia || "Sem instrução de IA cadastrada."}
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>

      {showModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-2xl rounded-2xl bg-white shadow-2xl">
            <div className="flex items-center justify-between border-b border-gray-100 px-6 py-4">
              <div>
                <h2 className="text-xl font-bold text-gray-900">
                  {editingTag ? "Editar tag oficial" : "Nova tag oficial"}
                </h2>
                <p className="text-sm text-gray-500">
                  Configure a cor visual da tag e a regra que a IA deve seguir para aplicá-la.
                </p>
              </div>
              <button type="button" onClick={closeModal} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100 hover:text-gray-700">
                <X size={18} />
              </button>
            </div>

            <form onSubmit={handleSubmit} className="space-y-5 p-6">
              <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_180px]">
                <div>
                  <label className="mb-1 block text-sm font-medium text-gray-700">Nome da tag</label>
                  <input
                    required
                    value={formData.nome}
                    onChange={(e) => setFormData((prev) => ({ ...prev, nome: e.target.value }))}
                    className="w-full rounded-xl border border-gray-200 px-4 py-2.5 text-gray-800 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                    placeholder="Ex: VIP, Reengajar, Urgente"
                  />
                </div>

                <div>
                  <label className="mb-1 block text-sm font-medium text-gray-700">Cor</label>
                  <input
                    type="color"
                    value={formData.cor}
                    onChange={(e) => setFormData((prev) => ({ ...prev, cor: e.target.value }))}
                    className="h-[46px] w-full rounded-xl border border-gray-200 bg-white px-2 py-1"
                  />
                </div>
              </div>

              <div>
                <label className="mb-1 block text-sm font-medium text-gray-700">Instrução para a IA</label>
                <textarea
                  rows={6}
                  value={formData.instrucao_ia}
                  onChange={(e) => setFormData((prev) => ({ ...prev, instrucao_ia: e.target.value }))}
                  className="w-full rounded-xl border border-gray-200 px-4 py-3 text-gray-800 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                  placeholder="Explique em quais cenários a IA deve aplicar esta tag ao lead."
                />
              </div>

              <div className="flex justify-end gap-3 pt-2">
                <button type="button" onClick={closeModal} className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm font-medium text-gray-600 hover:bg-gray-50">
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {saving ? "Salvando..." : editingTag ? "Salvar alterações" : "Criar tag"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}
    </div>
  );
}

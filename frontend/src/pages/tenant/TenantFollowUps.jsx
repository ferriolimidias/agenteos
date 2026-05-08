import { useEffect, useMemo, useState } from "react";
import { Clock3, Plus, Pencil, Trash2, X, TimerReset, RefreshCw } from "lucide-react";

import api from "../../services/api";
import { getActiveEmpresaId } from "../../utils/auth";

const initialForm = {
  nome: "",
  tempo_gatilho_minutos: "",
  objetivo_prompt: "",
  tag_aplicar_final: "",
  ativo: true,
};

function SkeletonCard() {
  return (
    <div className="animate-pulse rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="h-5 w-40 rounded-xl bg-slate-200" />
      <div className="mt-3 h-4 w-28 rounded-xl bg-slate-200" />
      <div className="mt-4 h-14 w-full rounded-xl bg-slate-200" />
    </div>
  );
}

export default function TenantFollowUps() {
  const empresaId = getActiveEmpresaId();
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [followups, setFollowups] = useState([]);
  const [tags, setTags] = useState([]);
  const [showModal, setShowModal] = useState(false);
  const [editingItem, setEditingItem] = useState(null);
  const [formData, setFormData] = useState(initialForm);

  const tagsMap = useMemo(() => {
    const map = new Map();
    tags.forEach((tag) => map.set(tag.id, tag));
    return map;
  }, [tags]);

  const carregarDados = async () => {
    if (!empresaId) {
      setLoading(false);
      return;
    }
    try {
      setLoading(true);
      const [followupsRes, tagsRes] = await Promise.all([
        api.get(`/empresas/${empresaId}/followups`, { timeout: 15000 }),
        api.get(`/empresas/${empresaId}/crm/tags/oficiais`, { timeout: 15000 }),
      ]);
      setFollowups(Array.isArray(followupsRes.data) ? followupsRes.data : []);
      setTags(Array.isArray(tagsRes.data) ? tagsRes.data : []);
    } catch (error) {
      console.error("Erro ao carregar cadências:", error);
      setFollowups([]);
      setTags([]);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    carregarDados();
  }, [empresaId]);

  const openCreate = () => {
    setEditingItem(null);
    setFormData(initialForm);
    setShowModal(true);
  };

  const openEdit = (item) => {
    setEditingItem(item);
    setFormData({
      nome: item.nome || "",
      tempo_gatilho_minutos: String(item.tempo_gatilho_minutos ?? ""),
      objetivo_prompt: item.objetivo_prompt || "",
      tag_aplicar_final: item.tag_aplicar_final || "",
      ativo: Boolean(item.ativo),
    });
    setShowModal(true);
  };

  const closeModal = () => {
    setShowModal(false);
    setEditingItem(null);
    setFormData(initialForm);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    if (!empresaId) return;
    try {
      setSaving(true);
      const payload = {
        nome: String(formData.nome || "").trim(),
        tempo_gatilho_minutos: Number(formData.tempo_gatilho_minutos || 0),
        objetivo_prompt: String(formData.objetivo_prompt || "").trim(),
        tag_aplicar_final: String(formData.tag_aplicar_final || "").trim() || null,
        ativo: Boolean(formData.ativo),
      };
      if (editingItem?.id) {
        await api.put(`/empresas/${empresaId}/followups/${editingItem.id}`, payload);
      } else {
        await api.post(`/empresas/${empresaId}/followups`, payload);
      }
      await carregarDados();
      closeModal();
    } catch (error) {
      console.error("Erro ao salvar cadência:", error);
      alert(error.response?.data?.detail || "Não foi possível salvar a cadência.");
    } finally {
      setSaving(false);
    }
  };

  const handleDelete = async (item) => {
    if (!empresaId || !item?.id) return;
    if (!window.confirm(`Excluir a cadência "${item.nome}"?`)) return;
    try {
      setDeletingId(item.id);
      await api.delete(`/empresas/${empresaId}/followups/${item.id}`);
      await carregarDados();
    } catch (error) {
      console.error("Erro ao excluir cadência:", error);
      alert(error.response?.data?.detail || "Não foi possível excluir a cadência.");
    } finally {
      setDeletingId(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col gap-3 md:flex-row md:items-center md:justify-between">
        <div>
          <h1 className="text-2xl font-semibold text-slate-900">Cadências / Follow-up</h1>
          <p className="mt-1 text-sm text-slate-500">
            Configure disparos automáticos de retomada para leads inativos.
          </p>
        </div>
        <div className="flex gap-2">
          <button
            type="button"
            onClick={carregarDados}
            className="inline-flex items-center gap-2 rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm font-medium text-slate-700 transition-colors hover:bg-slate-50"
          >
            <RefreshCw size={15} />
            Atualizar
          </button>
          <button
            type="button"
            onClick={openCreate}
            className="inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500"
          >
            <Plus size={16} />
            Nova Cadência
          </button>
        </div>
      </div>

      {loading ? (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          <SkeletonCard />
          <SkeletonCard />
          <SkeletonCard />
        </div>
      ) : followups.length === 0 ? (
        <div className="flex items-center justify-center py-6">
          <div className="w-full max-w-xl rounded-xl border border-slate-200 bg-white p-8 text-center shadow-sm">
            <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-indigo-50 text-indigo-600">
              <TimerReset className="h-6 w-6" />
            </div>
            <h2 className="text-lg font-semibold text-slate-900">Nenhuma cadência configurada</h2>
            <p className="mt-2 text-sm text-slate-500">
              Crie uma cadência para retomar automaticamente leads inativos.
            </p>
            <button
              type="button"
              onClick={openCreate}
              className="mt-5 inline-flex items-center gap-2 rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-all hover:bg-indigo-700"
            >
              <Plus size={15} />
              Nova Cadência
            </button>
          </div>
        </div>
      ) : (
        <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
          {followups.map((item) => {
            const tag = item.tag_aplicar_final ? tagsMap.get(item.tag_aplicar_final) : null;
            return (
              <div
                key={item.id}
                className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm transition-all hover:shadow-md"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="truncate text-lg font-semibold text-slate-900">{item.nome}</h3>
                    <p className="mt-1 flex items-center gap-1 text-sm text-indigo-600">
                      <Clock3 size={14} />
                      {item.tempo_gatilho_minutos} min
                    </p>
                  </div>
                  <span
                    className={`inline-flex items-center rounded-full border px-2.5 py-1 text-xs font-semibold ${
                      item.ativo
                        ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                        : "border-amber-200 bg-amber-50 text-amber-700"
                    }`}
                  >
                    {item.ativo ? "Ativa" : "Inativa"}
                  </span>
                </div>

                <p className="mt-4 line-clamp-3 text-sm text-slate-500">{item.objetivo_prompt}</p>

                <div className="mt-4 text-xs text-slate-500">
                  Tag final:{" "}
                  <span className="font-medium text-slate-800">
                    {tag?.nome || (item.tag_aplicar_final ? "Tag não encontrada" : "Nenhuma")}
                  </span>
                </div>

                <div className="mt-5 flex items-center gap-2">
                  <button
                    type="button"
                    onClick={() => openEdit(item)}
                    className="inline-flex items-center gap-1 rounded-lg border border-slate-200 bg-white px-3 py-1.5 text-xs font-medium text-slate-700 transition-colors hover:bg-slate-50"
                  >
                    <Pencil size={13} />
                    Editar
                  </button>
                  <button
                    type="button"
                    disabled={deletingId === item.id}
                    onClick={() => handleDelete(item)}
                    className="inline-flex items-center gap-1 rounded-lg border border-red-500/30 bg-red-500/10 px-3 py-1.5 text-xs font-medium text-red-300 hover:bg-red-500/20 disabled:opacity-60"
                  >
                    <Trash2 size={13} />
                    {deletingId === item.id ? "Excluindo..." : "Excluir"}
                  </button>
                </div>
              </div>
            );
          })}
        </div>
      )}

      {showModal && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
          <div className="w-full max-w-2xl rounded-2xl border border-slate-200 bg-white p-6 shadow-xl">
            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-lg font-semibold text-slate-900">
                {editingItem ? "Editar Cadência" : "Nova Cadência"}
              </h2>
              <button
                type="button"
                onClick={closeModal}
                className="rounded-lg border border-slate-200 p-2 text-slate-500 transition-colors hover:bg-slate-100"
              >
                <X size={14} />
              </button>
            </div>

            <form onSubmit={handleSubmit} className="space-y-4">
              <div>
                <label className="mb-1 block text-sm font-medium text-slate-700">Nome da Cadência</label>
                <input
                  required
                  value={formData.nome}
                  onChange={(e) => setFormData((prev) => ({ ...prev, nome: e.target.value }))}
                  className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition-all focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                  placeholder="Ex: Recuperação 1h"
                />
              </div>

              <div>
                <label className="mb-1 block text-sm font-medium text-slate-700">Tempo de Disparo (Minutos)</label>
                <input
                  required
                  type="number"
                  min={1}
                  value={formData.tempo_gatilho_minutos}
                  onChange={(e) => setFormData((prev) => ({ ...prev, tempo_gatilho_minutos: e.target.value }))}
                  className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition-all focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                  placeholder="Ex: 60 para 1h, 1440 para 24h"
                />
              </div>

              <div>
                <label className="mb-1 block text-sm font-medium text-slate-700">Objetivo</label>
                <textarea
                  required
                  rows={4}
                  value={formData.objetivo_prompt}
                  onChange={(e) => setFormData((prev) => ({ ...prev, objetivo_prompt: e.target.value }))}
                  className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition-all focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                  placeholder="Ex: O lead parou de responder. Ofereça 10% de desconto de forma amigável."
                />
              </div>

              <div>
                <label className="mb-1 block text-sm font-medium text-slate-700">Tag Final (Opcional)</label>
                <select
                  value={formData.tag_aplicar_final}
                  onChange={(e) => setFormData((prev) => ({ ...prev, tag_aplicar_final: e.target.value }))}
                  className="w-full rounded-xl border border-slate-200 bg-white px-3 py-2 text-sm text-slate-900 outline-none transition-all focus:border-indigo-500 focus:ring-2 focus:ring-indigo-500"
                >
                  <option value="">Nenhuma</option>
                  {tags.map((tag) => (
                    <option key={tag.id} value={tag.id}>
                      {tag.nome}
                    </option>
                  ))}
                </select>
              </div>

              <label className="flex items-center gap-2 text-sm text-slate-700">
                <input
                  type="checkbox"
                  checked={Boolean(formData.ativo)}
                  onChange={(e) => setFormData((prev) => ({ ...prev, ativo: e.target.checked }))}
                  className="h-4 w-4 rounded border-slate-300 text-indigo-600 focus:ring-indigo-500"
                />
                Cadência ativa
              </label>

              <div className="flex justify-end gap-2 pt-2">
                <button
                  type="button"
                  onClick={closeModal}
                  className="rounded-xl border border-slate-200 bg-white px-4 py-2 text-sm text-slate-600 transition-colors hover:bg-slate-50"
                >
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={saving}
                  className="inline-flex items-center gap-2 rounded-xl bg-indigo-600 px-4 py-2 text-sm font-medium text-white hover:bg-indigo-500 disabled:opacity-60"
                >
                  <TimerReset size={15} />
                  {saving ? "Salvando..." : editingItem ? "Salvar Alterações" : "Criar Cadência"}
                </button>
              </div>
            </form>
          </div>
        </div>
      )}
    </div>
  );
}


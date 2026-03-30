import { useEffect, useMemo, useState } from "react";
import { Layers3, Palette, Pencil, Plus, RefreshCw, Tag, Trash2, X } from "lucide-react";

import api from "../../services/api";
import { getActiveEmpresaId } from "../../utils/auth";

const initialTagForm = {
  nome: "",
  cor: "#2563eb",
  instrucao_ia: "",
  grupo_id: "",
  disparar_conversao_ads: false,
  acao_fechamento: false,
};

const initialGroupForm = {
  nome: "",
  cor: "#2563eb",
  ordem: 0,
};

export default function GestaoTags() {
  const empresaId = getActiveEmpresaId();
  const [tags, setTags] = useState([]);
  const [grupos, setGrupos] = useState([]);
  const [loading, setLoading] = useState(true);
  const [groupsLoading, setGroupsLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [groupSaving, setGroupSaving] = useState(false);
  const [deletingId, setDeletingId] = useState(null);
  const [deletingGroupId, setDeletingGroupId] = useState(null);
  const [showModal, setShowModal] = useState(false);
  const [showGroupModal, setShowGroupModal] = useState(false);
  const [editingTag, setEditingTag] = useState(null);
  const [editingGroup, setEditingGroup] = useState(null);
  const [formData, setFormData] = useState(initialTagForm);
  const [groupFormData, setGroupFormData] = useState(initialGroupForm);
  const tagForm = formData;
  const setTagForm = (updater) => {
    setFormData((prev) => (typeof updater === "function" ? updater(prev) : updater));
  };

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

  const fetchGrupos = async () => {
    if (!empresaId) return;
    try {
      setGroupsLoading(true);
      const res = await api.get(`/empresas/${empresaId}/crm/tags/grupos`);
      setGrupos(res.data || []);
    } catch (err) {
      console.error("Erro ao carregar grupos de tags:", err);
      setGrupos([]);
    } finally {
      setGroupsLoading(false);
    }
  };

  useEffect(() => {
    fetchTags();
    fetchGrupos();
  }, [empresaId]);

  const gruposMap = useMemo(() => {
    const output = new Map();
    grupos.forEach((grupo) => output.set(grupo.id, grupo));
    return output;
  }, [grupos]);

  const refreshAll = async () => {
    await Promise.all([fetchTags(), fetchGrupos()]);
  };

  const closeModal = () => {
    setShowModal(false);
    setEditingTag(null);
    setFormData(initialTagForm);
  };

  const closeGroupModal = () => {
    setShowGroupModal(false);
    setEditingGroup(null);
    setGroupFormData(initialGroupForm);
  };

  const openCreateModal = () => {
    setEditingTag(null);
    setFormData(initialTagForm);
    setShowModal(true);
  };

  const openCreateGroupModal = () => {
    setEditingGroup(null);
    setGroupFormData(initialGroupForm);
    setShowGroupModal(true);
  };

  const openEditModal = (tag) => {
    setEditingTag(tag);
    setFormData({
      nome: tag.nome || "",
      cor: tag.cor || "#2563eb",
      instrucao_ia: tag.instrucao_ia || "",
      grupo_id: tag.grupo_id || "",
      disparar_conversao_ads: Boolean(tag.disparar_conversao_ads),
      acao_fechamento: Boolean(tag.acao_fechamento),
    });
    setShowModal(true);
  };

  const openEditGroupModal = (grupo) => {
    setEditingGroup(grupo);
    setGroupFormData({
      nome: grupo.nome || "",
      cor: grupo.cor || "#2563eb",
      ordem: grupo.ordem ?? 0,
    });
    setShowGroupModal(true);
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    try {
      setSaving(true);
      const payload = {
        ...formData,
        grupo_id: formData.grupo_id || null,
      };
      if (editingTag) {
        await api.put(`/empresas/${empresaId}/crm/tags/oficiais/${editingTag.id}`, payload);
      } else {
        await api.post(`/empresas/${empresaId}/crm/tags/oficiais`, payload);
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

  const handleGroupSubmit = async (e) => {
    e.preventDefault();
    try {
      setGroupSaving(true);
      const payload = {
        ...groupFormData,
        ordem: Number(groupFormData.ordem || 0),
      };
      if (editingGroup) {
        await api.put(`/empresas/${empresaId}/crm/tags/grupos/${editingGroup.id}`, payload);
      } else {
        await api.post(`/empresas/${empresaId}/crm/tags/grupos`, payload);
      }
      await Promise.all([fetchGrupos(), fetchTags()]);
      closeGroupModal();
    } catch (err) {
      console.error("Erro ao salvar grupo:", err);
      alert(err.response?.data?.detail || "Não foi possível salvar o grupo.");
    } finally {
      setGroupSaving(false);
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

  const handleDeleteGroup = async (grupo) => {
    if (!window.confirm(`Excluir o grupo "${grupo.nome}"? As tags associadas ficarão sem grupo.`)) return;
    try {
      setDeletingGroupId(grupo.id);
      await api.delete(`/empresas/${empresaId}/crm/tags/grupos/${grupo.id}`);
      await Promise.all([fetchGrupos(), fetchTags()]);
    } catch (err) {
      console.error("Erro ao excluir grupo:", err);
      alert(err.response?.data?.detail || "Não foi possível excluir o grupo.");
    } finally {
      setDeletingGroupId(null);
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
            Cadastre grupos e tags oficiais do CRM para organizar departamentos, campanhas e as futuras abas do Inbox.
          </p>
        </div>

        <div className="flex flex-wrap gap-2">
          <button
            type="button"
            onClick={refreshAll}
            className="inline-flex items-center gap-2 rounded-xl border border-gray-200 bg-white px-4 py-2.5 text-sm font-medium text-gray-700 transition-colors hover:bg-gray-50"
          >
            <RefreshCw size={16} />
            Atualizar
          </button>
          <button
            type="button"
            onClick={openCreateGroupModal}
            className="inline-flex items-center gap-2 rounded-xl border border-blue-200 bg-blue-50 px-4 py-2.5 text-sm font-medium text-blue-700 transition-colors hover:bg-blue-100"
          >
            <Layers3 size={16} />
            Novo Grupo
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
        <div className="border-b border-gray-100 px-6 py-5">
          <div className="flex items-center justify-between gap-4">
            <div>
              <h2 className="flex items-center gap-2 text-lg font-semibold text-gray-900">
                <Layers3 size={18} className="text-blue-600" />
                Grupos de Tags
              </h2>
              <p className="mt-1 text-sm text-gray-500">
                Defina departamentos como Vendas, Suporte ou Pedagógico para estruturar a navegação do Inbox.
              </p>
            </div>
            <button
              type="button"
              onClick={openCreateGroupModal}
              className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white transition-colors hover:bg-blue-700"
            >
              <Plus size={16} />
              Novo Grupo
            </button>
          </div>
        </div>

        <div className="p-6">
          {groupsLoading ? (
            <div className="flex items-center justify-center py-10 text-gray-500">
              <RefreshCw size={18} className="mr-2 animate-spin" />
              Carregando grupos...
            </div>
          ) : grupos.length === 0 ? (
            <div className="rounded-2xl border-2 border-dashed border-gray-200 bg-gray-50 px-6 py-10 text-center">
              <Layers3 className="mx-auto mb-3 text-gray-300" size={34} />
              <p className="text-sm font-medium text-gray-700">Nenhum grupo cadastrado.</p>
              <p className="mt-1 text-sm text-gray-500">
                Crie grupos para separar tags por departamento e preparar as abas do Inbox.
              </p>
            </div>
          ) : (
            <div className="overflow-hidden rounded-2xl border border-gray-200">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">Nome</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">Cor</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">Ordem</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-gray-500">Ações</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 bg-white">
                  {grupos.map((grupo) => (
                    <tr key={grupo.id}>
                      <td className="px-4 py-3">
                        <span
                          className="inline-flex rounded-full border px-3 py-1 text-xs font-semibold"
                          style={{
                            backgroundColor: `${(grupo.cor || "#2563eb")}18`,
                            borderColor: `${(grupo.cor || "#2563eb")}55`,
                            color: grupo.cor || "#2563eb",
                          }}
                        >
                          {grupo.nome}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm text-gray-600">{grupo.cor || "Sem cor"}</td>
                      <td className="px-4 py-3 text-sm text-gray-600">{grupo.ordem ?? 0}</td>
                      <td className="px-4 py-3">
                        <div className="flex justify-end gap-2">
                          <button
                            type="button"
                            onClick={() => openEditGroupModal(grupo)}
                            className="inline-flex items-center gap-2 rounded-lg border border-blue-200 bg-white px-3 py-2 text-sm text-blue-700 transition-colors hover:bg-blue-50"
                          >
                            <Pencil size={14} />
                            Editar
                          </button>
                          <button
                            type="button"
                            onClick={() => handleDeleteGroup(grupo)}
                            disabled={deletingGroupId === grupo.id}
                            className="inline-flex items-center gap-2 rounded-lg border border-red-200 bg-white px-3 py-2 text-sm text-red-600 transition-colors hover:bg-red-50 disabled:cursor-not-allowed disabled:opacity-60"
                          >
                            {deletingGroupId === grupo.id ? <RefreshCw size={14} className="animate-spin" /> : <Trash2 size={14} />}
                            Excluir
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      <div className="rounded-2xl border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-100 px-6 py-5">
          <h2 className="text-lg font-semibold text-gray-900">Tags Oficiais</h2>
          <p className="mt-1 text-sm text-gray-500">
            Vincule cada tag ao grupo correto para organizar classificações da IA, campanhas e navegação futura.
          </p>
        </div>

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
                Crie tags como VIP, Reengajar, Urgente ou Qualificado e associe-as aos grupos certos.
              </p>
            </div>
          ) : (
            <div className="overflow-hidden rounded-2xl border border-gray-200">
              <table className="min-w-full divide-y divide-gray-200">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">Tag</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">Grupo</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">Cor</th>
                    <th className="px-4 py-3 text-left text-xs font-semibold uppercase tracking-wide text-gray-500">Instrução IA</th>
                    <th className="px-4 py-3 text-right text-xs font-semibold uppercase tracking-wide text-gray-500">Ações</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-gray-100 bg-white">
                  {tags.map((tag) => {
                    const grupo = tag.grupo_id ? gruposMap.get(tag.grupo_id) : null;
                    return (
                      <tr key={tag.id}>
                        <td className="px-4 py-3">
                          <span
                            className="inline-flex rounded-full border px-3 py-1 text-xs font-semibold"
                            style={{ backgroundColor: `${tag.cor}18`, borderColor: `${tag.cor}55`, color: tag.cor }}
                          >
                            {tag.nome}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-600">
                          {grupo ? (
                            <span
                              className="inline-flex rounded-full border px-2.5 py-1 text-xs font-semibold"
                              style={{
                                backgroundColor: `${(grupo.cor || "#94a3b8")}18`,
                                borderColor: `${(grupo.cor || "#94a3b8")}55`,
                                color: grupo.cor || "#64748b",
                              }}
                            >
                              {grupo.nome}
                            </span>
                          ) : (
                            <span className="text-gray-400">Sem grupo</span>
                          )}
                        </td>
                        <td className="px-4 py-3 text-sm text-gray-600">
                          <span className="inline-flex items-center gap-2">
                            <Palette size={14} style={{ color: tag.cor }} />
                            {tag.cor}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-sm leading-6 text-gray-700">
                          {tag.instrucao_ia || "Sem instrução de IA cadastrada."}
                        </td>
                        <td className="px-4 py-3">
                          <div className="flex justify-end gap-2">
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
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      </div>

      {showGroupModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="flex max-h-[90vh] w-full max-w-xl flex-col rounded-lg bg-white shadow-xl">
            <div className="flex shrink-0 items-center justify-between border-b border-gray-100 p-4 md:p-6">
              <div>
                <h2 className="text-xl font-bold text-gray-900">
                  {editingGroup ? "Editar grupo" : "Novo grupo"}
                </h2>
                <p className="text-sm text-gray-500">
                  Configure o departamento que organizará as tags e as futuras abas do Inbox.
                </p>
              </div>
              <button type="button" onClick={closeGroupModal} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100 hover:text-gray-700">
                <X size={18} />
              </button>
            </div>

            <form onSubmit={handleGroupSubmit} className="flex flex-1 flex-col overflow-hidden">
              <div className="flex-1 overflow-y-auto p-4 md:p-6">
                <div className="grid gap-4 md:grid-cols-[minmax(0,1fr)_180px_140px]">
                  <div>
                    <label className="mb-1 block text-sm font-medium text-gray-700">Nome do grupo</label>
                    <input
                      required
                      value={groupFormData.nome}
                      onChange={(e) => setGroupFormData((prev) => ({ ...prev, nome: e.target.value }))}
                      className="w-full rounded-xl border border-gray-200 px-4 py-2.5 text-gray-800 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                      placeholder="Ex: Vendas, Suporte"
                    />
                  </div>

                  <div>
                    <label className="mb-1 block text-sm font-medium text-gray-700">Cor</label>
                    <input
                      type="color"
                      value={groupFormData.cor}
                      onChange={(e) => setGroupFormData((prev) => ({ ...prev, cor: e.target.value }))}
                      className="h-[46px] w-full rounded-xl border border-gray-200 bg-white px-2 py-1"
                    />
                  </div>

                  <div>
                    <label className="mb-1 block text-sm font-medium text-gray-700">Ordem</label>
                    <input
                      type="number"
                      min="0"
                      value={groupFormData.ordem}
                      onChange={(e) => setGroupFormData((prev) => ({ ...prev, ordem: e.target.value }))}
                      className="w-full rounded-xl border border-gray-200 px-4 py-2.5 text-gray-800 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                    />
                  </div>
                </div>
              </div>
              <div className="flex shrink-0 justify-end gap-3 rounded-b-lg border-t border-gray-100 bg-gray-50 p-4 md:p-6">
                <button type="button" onClick={closeGroupModal} className="rounded-xl border border-gray-200 px-4 py-2.5 text-sm font-medium text-gray-600 hover:bg-gray-50">
                  Cancelar
                </button>
                <button
                  type="submit"
                  disabled={groupSaving}
                  className="rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-medium text-white hover:bg-blue-700 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {groupSaving ? "Salvando..." : editingGroup ? "Salvar alterações" : "Criar grupo"}
                </button>
              </div>
            </form>
          </div>
        </div>
      ) : null}

      {showModal ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4">
          <div className="flex max-h-[90vh] w-full max-w-2xl flex-col rounded-lg bg-white shadow-xl">
            <div className="flex shrink-0 items-center justify-between border-b border-gray-100 p-4 md:p-6">
              <div>
                <h2 className="text-xl font-bold text-gray-900">
                  {editingTag ? "Editar tag oficial" : "Nova tag oficial"}
                </h2>
                <p className="text-sm text-gray-500">
                  Configure a cor visual da tag, vincule-a ao grupo correto e defina a regra de classificação da IA.
                </p>
              </div>
              <button type="button" onClick={closeModal} className="rounded-lg p-2 text-gray-400 hover:bg-gray-100 hover:text-gray-700">
                <X size={18} />
              </button>
            </div>

            <form onSubmit={handleSubmit} className="flex flex-1 flex-col overflow-hidden">
              <div className="flex-1 overflow-y-auto p-4 md:p-6">
                <div className="space-y-5">
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
                    <label className="mb-1 block text-sm font-medium text-gray-700">Grupo</label>
                    <select
                      value={formData.grupo_id}
                      onChange={(e) => setFormData((prev) => ({ ...prev, grupo_id: e.target.value }))}
                      className="w-full rounded-xl border border-gray-200 px-4 py-2.5 text-gray-800 outline-none transition-all focus:border-blue-500 focus:ring-2 focus:ring-blue-500"
                    >
                      <option value="">Sem grupo</option>
                      {grupos.map((grupo) => (
                        <option key={grupo.id} value={grupo.id}>
                          {grupo.nome}
                        </option>
                      ))}
                    </select>
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

                  <div className="rounded-xl border border-gray-200 bg-gray-50 px-4 py-3">
                    <div className="flex items-center justify-between gap-4">
                      <div>
                        <p className="text-sm font-semibold text-gray-800">Disparar Conversão de Ads</p>
                        <p className="text-xs text-gray-500">
                          Quando essa tag for aplicada, tentará disparar conversão se houver gclid/fbclid no lead.
                        </p>
                      </div>
                      <button
                        type="button"
                        role="switch"
                        aria-checked={formData.disparar_conversao_ads}
                        onClick={() =>
                          setFormData((prev) => ({
                            ...prev,
                            disparar_conversao_ads: !prev.disparar_conversao_ads,
                          }))
                        }
                        className={`relative inline-flex h-7 w-12 items-center rounded-full transition-colors ${
                          formData.disparar_conversao_ads ? "bg-blue-600" : "bg-gray-300"
                        }`}
                      >
                        <span
                          className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
                            formData.disparar_conversao_ads ? "translate-x-6" : "translate-x-1"
                          }`}
                        />
                      </button>
                    </div>
                  </div>

                  <div className="rounded-xl border border-gray-100 bg-gray-50 px-4 py-3 mt-3">
                    <div className="flex items-center justify-between gap-4">
                      <div>
                        <p className="text-sm font-semibold text-gray-800">Ação de Fechamento</p>
                        <p className="text-xs text-gray-500">
                          Quando essa tag for aplicada, o card do lead moverá automaticamente para a coluna Fechado do CRM.
                        </p>
                      </div>
                      <button
                        type="button"
                        role="switch"
                        aria-checked={tagForm.acao_fechamento}
                        onClick={() => setTagForm(prev => ({ ...prev, acao_fechamento: !prev.acao_fechamento }))}
                        className={`${
                          tagForm.acao_fechamento ? 'bg-primary-600' : 'bg-gray-200'
                        } relative inline-flex h-6 w-11 flex-shrink-0 cursor-pointer rounded-full border-2 border-transparent transition-colors duration-200 ease-in-out focus:outline-none`}
                      >
                        <span
                          className={`inline-block h-5 w-5 transform rounded-full bg-white shadow transition-transform ${
                            tagForm.acao_fechamento ? 'translate-x-5' : 'translate-x-0'
                          }`}
                          aria-hidden="true"
                        />
                      </button>
                    </div>
                  </div>
                </div>
              </div>
              <div className="flex shrink-0 justify-end gap-3 rounded-b-lg border-t border-gray-100 bg-gray-50 p-4 md:p-6">
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

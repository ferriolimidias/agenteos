import { useCallback, useEffect, useMemo, useState } from "react";
import { Columns3, Loader2, Pencil, Plus, Trash2, X } from "lucide-react";
import api from "../services/api";

/** Dispara em qualquer separador da mesma origem; o Kanban do tenant escuta e refaz o GET /crm. */
export const CRM_ETAPAS_ATUALIZADAS = "agenteos-crm-etapas-atualizadas";

export function notificarCrmEtapasAlteradas(empresaId) {
  const id = String(empresaId || "");
  if (!id) return;
  try {
    window.dispatchEvent(new CustomEvent(CRM_ETAPAS_ATUALIZADAS, { detail: { empresaId: id } }));
  } catch {
    /* ignore */
  }
  try {
    localStorage.setItem(`crm_etapas_revision_${id}`, String(Date.now()));
  } catch {
    /* ignore */
  }
}

function ordenarEtapas(lista) {
  if (!Array.isArray(lista)) return [];
  return [...lista].sort(
    (a, b) =>
      Number(a?.ordem ?? 0) - Number(b?.ordem ?? 0) || String(a?.id || "").localeCompare(String(b?.id || "")),
  );
}

export default function EmpresaCrmEtapasManager({ empresaId, onNotify }) {
  const [loading, setLoading] = useState(true);
  const [savingId, setSavingId] = useState(null);
  const [deletingId, setDeletingId] = useState(null);
  const [creating, setCreating] = useState(false);
  const [funilNome, setFunilNome] = useState("");
  const [etapas, setEtapas] = useState([]);
  const [novaEtapaNome, setNovaEtapaNome] = useState("");
  const [editRow, setEditRow] = useState(null);

  const carregar = useCallback(async () => {
    if (!empresaId) return;
    setLoading(true);
    try {
      const res = await api.get(`/empresas/${empresaId}/crm`);
      const data = res.data || {};
      setFunilNome(String(data.funil_nome || "Funil"));
      setEtapas(ordenarEtapas(data.etapas));
    } catch (err) {
      console.error("Erro ao carregar funil CRM:", err);
      setEtapas([]);
      onNotify?.("Não foi possível carregar as etapas do CRM.");
    } finally {
      setLoading(false);
    }
  }, [empresaId, onNotify]);

  useEffect(() => {
    void carregar();
  }, [carregar]);

  const maxOrdem = useMemo(() => {
    let m = 0;
    for (const e of etapas) {
      const o = Number(e?.ordem);
      if (Number.isFinite(o)) m = Math.max(m, o);
    }
    return m;
  }, [etapas]);

  const handleCriar = async () => {
    const nome = String(novaEtapaNome || "").trim();
    if (!nome || !empresaId) return;
    try {
      setCreating(true);
      await api.post(`/empresas/${empresaId}/crm/etapas`, {
        nome,
        tipo: "custom",
        ordem: maxOrdem + 1,
      });
      setNovaEtapaNome("");
      onNotify?.("Etapa criada. O Kanban do tenant atualiza automaticamente (se o CRM estiver aberto).");
      notificarCrmEtapasAlteradas(empresaId);
      await carregar();
    } catch (err) {
      console.error(err);
      onNotify?.(err.response?.data?.detail || "Falha ao criar etapa.");
    } finally {
      setCreating(false);
    }
  };

  const handleSalvarEdicao = async () => {
    if (!editRow?.id || !empresaId) return;
    const nome = String(editRow.nome || "").trim();
    if (!nome) {
      onNotify?.("Nome da etapa não pode ser vazio.");
      return;
    }
    const ordem = Number.parseInt(String(editRow.ordem), 10);
    if (!Number.isFinite(ordem)) {
      onNotify?.("Ordem deve ser um número inteiro.");
      return;
    }
    try {
      setSavingId(editRow.id);
      await api.put(`/empresas/${empresaId}/crm/etapas/${editRow.id}`, {
        nome,
        ordem,
      });
      setEditRow(null);
      onNotify?.("Etapa atualizada.");
      notificarCrmEtapasAlteradas(empresaId);
      await carregar();
    } catch (err) {
      console.error(err);
      onNotify?.(err.response?.data?.detail || "Falha ao salvar etapa.");
    } finally {
      setSavingId(null);
    }
  };

  const handleExcluir = async (etapa) => {
    if (!etapa?.id || !empresaId) return;
    const ok = window.confirm(
      `Excluir a etapa "${etapa.nome}"? Os leads desta coluna serão movidos para a etapa com menor ordem restante.`,
    );
    if (!ok) return;
    try {
      setDeletingId(etapa.id);
      await api.delete(`/empresas/${empresaId}/crm/etapas/${etapa.id}`);
      onNotify?.("Etapa removida.");
      notificarCrmEtapasAlteradas(empresaId);
      await carregar();
    } catch (err) {
      console.error(err);
      onNotify?.(err.response?.data?.detail || "Falha ao excluir etapa.");
    } finally {
      setDeletingId(null);
    }
  };

  if (!empresaId) return null;

  return (
    <div className="space-y-4 rounded-xl border border-[#2d2d2d] bg-[#131313] p-5 shadow-sm">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h3 className="flex items-center gap-2 text-sm font-semibold text-white">
            <Columns3 size={16} className="text-indigo-300" />
            Gestão de Funil (etapas reais do CRM)
          </h3>
          <p className="mt-1 max-w-2xl text-xs text-gray-500">
            Estas linhas alteram a tabela <span className="font-mono text-[11px]">crm_etapas</span> — as mesmas colunas
            do Kanban no painel do tenant. As &quot;etapas&quot; criadas como tags em outra tela são só rótulos de
            sincronização; não substituem estas etapas.
          </p>
          <p className="mt-1 text-xs text-gray-600">
            Funil ativo: <span className="text-gray-400">{funilNome}</span>
          </p>
        </div>
      </div>

      <div className="flex flex-wrap items-end gap-2 rounded-lg border border-[#2d2d2d] bg-[#181818] p-3">
        <div className="min-w-[200px] flex-1">
          <label className="mb-1 block text-[11px] uppercase tracking-wide text-gray-500">Nova etapa</label>
          <input
            value={novaEtapaNome}
            onChange={(e) => setNovaEtapaNome(e.target.value)}
            placeholder="Ex.: Fase 1 - LeoFT"
            className="w-full rounded-lg border border-[#2d2d2d] bg-[#121212] px-3 py-2 text-sm text-gray-200 outline-none focus:border-indigo-500"
          />
        </div>
        <button
          type="button"
          disabled={creating || !String(novaEtapaNome || "").trim()}
          onClick={() => void handleCriar()}
          className="inline-flex items-center gap-2 rounded-lg border border-indigo-500/40 bg-indigo-600/25 px-3 py-2 text-xs font-semibold text-indigo-100 transition-colors hover:bg-indigo-600/35 disabled:opacity-50"
        >
          {creating ? <Loader2 size={14} className="animate-spin" /> : <Plus size={14} />}
          Adicionar
        </button>
      </div>

      {loading ? (
        <div className="flex items-center gap-2 py-8 text-sm text-gray-500">
          <Loader2 size={18} className="animate-spin text-indigo-400" />
          Carregando etapas…
        </div>
      ) : etapas.length === 0 ? (
        <p className="py-6 text-sm text-gray-500">Nenhuma etapa encontrada. Abra o CRM do tenant uma vez ou adicione uma etapa acima.</p>
      ) : (
        <div className="overflow-x-auto rounded-lg border border-[#2d2d2d]">
          <table className="w-full min-w-[520px] text-left text-sm">
            <thead className="border-b border-[#2d2d2d] bg-[#181818] text-[11px] uppercase tracking-wide text-gray-500">
              <tr>
                <th className="px-3 py-2 font-medium">Ordem</th>
                <th className="px-3 py-2 font-medium">Nome (coluna Kanban)</th>
                <th className="px-3 py-2 font-medium">Tipo</th>
                <th className="px-3 py-2 font-medium">Leads</th>
                <th className="px-3 py-2 text-right font-medium">Ações</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-[#2a2a2c]">
              {etapas.map((et) => (
                <tr key={et.id} className="bg-[#141414] hover:bg-[#181818]">
                  <td className="px-3 py-2 font-mono text-xs text-gray-400">{et.ordem ?? 0}</td>
                  <td className="px-3 py-2 font-medium text-gray-100">{et.nome}</td>
                  <td className="px-3 py-2 text-xs text-gray-500">{et.tipo || "—"}</td>
                  <td className="px-3 py-2 text-xs text-gray-400">{Array.isArray(et.leads) ? et.leads.length : 0}</td>
                  <td className="px-3 py-2 text-right">
                    <div className="flex justify-end gap-1">
                      <button
                        type="button"
                        onClick={() =>
                          setEditRow({
                            id: et.id,
                            nome: et.nome || "",
                            ordem: et.ordem ?? 0,
                          })
                        }
                        className="rounded-md border border-[#2d2d2d] p-1.5 text-gray-400 transition-colors hover:border-indigo-500/40 hover:text-indigo-200"
                        title="Editar"
                      >
                        <Pencil size={14} />
                      </button>
                      <button
                        type="button"
                        disabled={deletingId === et.id || etapas.length <= 1}
                        onClick={() => void handleExcluir(et)}
                        className="rounded-md border border-[#2d2d2d] p-1.5 text-gray-400 transition-colors hover:border-red-500/40 hover:text-red-300 disabled:opacity-40"
                        title={etapas.length <= 1 ? "Mantenha pelo menos uma etapa" : "Excluir"}
                      >
                        {deletingId === et.id ? <Loader2 size={14} className="animate-spin" /> : <Trash2 size={14} />}
                      </button>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {editRow ? (
        <div className="fixed inset-0 z-[80] flex items-center justify-center bg-black/60 p-4">
          <div className="w-full max-w-md rounded-xl border border-[#2d2d2d] bg-[#151515] p-5 shadow-xl">
            <div className="mb-4 flex items-start justify-between gap-2">
              <h4 className="text-sm font-semibold text-white">Editar etapa</h4>
              <button
                type="button"
                onClick={() => setEditRow(null)}
                className="rounded-lg p-1 text-gray-500 hover:bg-[#242424] hover:text-gray-200"
              >
                <X size={16} />
              </button>
            </div>
            <div className="space-y-3">
              <div>
                <label className="mb-1 block text-[11px] uppercase tracking-wide text-gray-500">Nome</label>
                <input
                  value={editRow.nome}
                  onChange={(e) => setEditRow((p) => ({ ...p, nome: e.target.value }))}
                  className="w-full rounded-lg border border-[#2d2d2d] bg-[#121212] px-3 py-2 text-sm text-gray-200 outline-none focus:border-indigo-500"
                />
              </div>
              <div>
                <label className="mb-1 block text-[11px] uppercase tracking-wide text-gray-500">Ordem (colunas da esquerda para a direita)</label>
                <input
                  type="number"
                  value={editRow.ordem}
                  onChange={(e) => setEditRow((p) => ({ ...p, ordem: e.target.value }))}
                  className="w-full rounded-lg border border-[#2d2d2d] bg-[#121212] px-3 py-2 text-sm text-gray-200 outline-none focus:border-indigo-500"
                />
              </div>
            </div>
            <div className="mt-5 flex justify-end gap-2">
              <button
                type="button"
                onClick={() => setEditRow(null)}
                className="rounded-lg border border-[#2d2d2d] px-3 py-2 text-xs font-medium text-gray-400 hover:bg-[#1f1f1f]"
              >
                Cancelar
              </button>
              <button
                type="button"
                disabled={Boolean(savingId)}
                onClick={() => void handleSalvarEdicao()}
                className="rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
              >
                {savingId ? "Salvando…" : "Salvar"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}

import { useCallback, useEffect, useMemo, useState } from "react";
import { Loader2, Plus, Save, Trash2 } from "lucide-react";

import api from "../services/api";

function newRuleKey() {
  return `rule-${Date.now()}-${Math.random().toString(36).slice(2, 9)}`;
}

export default function FunnelRoutingManager({ empresaId, onNotify }) {
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [loadError, setLoadError] = useState("");

  const [matchOrder, setMatchOrder] = useState("tags_first");
  const [defaultEspecialistaId, setDefaultEspecialistaId] = useState("");
  const [rules, setRules] = useState([]);

  const [especialistas, setEspecialistas] = useState([]);
  const [etapas, setEtapas] = useState([]);
  const [tags, setTags] = useState([]);

  const notify = useCallback(
    (msg) => {
      if (typeof onNotify === "function") onNotify(msg);
    },
    [onNotify],
  );

  const carregarReferencias = useCallback(async () => {
    if (!empresaId) return;
    const [resEsp, resCrm, resTags] = await Promise.all([
      api.get(`/admin/orquestrador/empresas/${empresaId}/especialistas`).catch(() => ({ data: [] })),
      api.get(`/empresas/${empresaId}/crm`).catch(() => ({ data: null })),
      api.get(`/empresas/${empresaId}/crm/tags/oficiais`).catch(() => ({ data: [] })),
    ]);
    const listaEsp = Array.isArray(resEsp.data) ? resEsp.data : [];
    setEspecialistas(listaEsp);
    const et = Array.isArray(resCrm.data?.etapas) ? resCrm.data.etapas : [];
    setEtapas(et);
    const tg = Array.isArray(resTags.data) ? resTags.data : [];
    setTags(tg);
  }, [empresaId]);

  const carregarFunnel = useCallback(async () => {
    if (!empresaId) return;
    setLoading(true);
    setLoadError("");
    try {
      await carregarReferencias();
      const res = await api.get(`/empresas/${empresaId}/funnel-routing`);
      const data = res.data || {};
      setMatchOrder(data.match_order === "etapa_first" ? "etapa_first" : "tags_first");
      setDefaultEspecialistaId(data.default_especialista_id ? String(data.default_especialista_id) : "");
      const rawRules = Array.isArray(data.rules) ? data.rules : [];
      setRules(
        rawRules.map((r) => ({
          _key: newRuleKey(),
          especialista_id: r.especialista_id ? String(r.especialista_id) : "",
          etapa_ids: Array.isArray(r.etapa_ids) ? r.etapa_ids.map(String) : [],
          tag_ids: Array.isArray(r.tag_ids) ? r.tag_ids.map(String) : [],
          ordem: typeof r.ordem === "number" ? r.ordem : parseInt(String(r.ordem || 0), 10) || 0,
        })),
      );
    } catch (err) {
      console.error(err);
      setLoadError(err.response?.data?.detail || "Não foi possível carregar o roteamento de funil.");
    } finally {
      setLoading(false);
    }
  }, [empresaId, carregarReferencias]);

  useEffect(() => {
    carregarFunnel();
  }, [carregarFunnel]);

  const adicionarRegra = () => {
    setRules((prev) => [
      ...prev,
      {
        _key: newRuleKey(),
        especialista_id: "",
        etapa_ids: [],
        tag_ids: [],
        ordem: 100,
      },
    ]);
  };

  const removerRegra = (key) => {
    setRules((prev) => prev.filter((r) => r._key !== key));
  };

  const atualizarRegra = (key, patch) => {
    setRules((prev) => prev.map((r) => (r._key === key ? { ...r, ...patch } : r)));
  };

  const toggleIdEmLista = (lista, id, checked) => {
    const sid = String(id);
    if (checked) return lista.includes(sid) ? lista : [...lista, sid];
    return lista.filter((x) => String(x) !== sid);
  };

  const salvar = async () => {
    if (!empresaId) return;
    const semEspecialista = rules.some((r) => !String(r.especialista_id || "").trim());
    if (rules.length > 0 && semEspecialista) {
      notify("Cada regra precisa de um especialista selecionado.");
      return;
    }
    setSaving(true);
    try {
      const body = {
        match_order: matchOrder,
        default_especialista_id: defaultEspecialistaId.trim() || null,
        rules: rules.map((r) => ({
          especialista_id: r.especialista_id,
          tag_ids: r.tag_ids,
          etapa_ids: r.etapa_ids,
          ordem: Number(r.ordem) || 0,
        })),
      };
      await api.patch(`/empresas/${empresaId}/funnel-routing`, body);
      notify("Roteamento de funil salvo.");
      await carregarFunnel();
    } catch (err) {
      console.error(err);
      notify(err.response?.data?.detail || "Falha ao salvar roteamento.");
    } finally {
      setSaving(false);
    }
  };

  const mapaEtapas = useMemo(() => {
    const m = new Map();
    etapas.forEach((e) => m.set(String(e.id), String(e.nome || e.id)));
    return m;
  }, [etapas]);

  const mapaTags = useMemo(() => {
    const m = new Map();
    tags.forEach((t) => m.set(String(t.id), String(t.nome || t.id)));
    return m;
  }, [tags]);

  if (loading) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-[#2d2d2d] bg-[#131313] p-6 text-sm text-gray-400">
        <Loader2 className="h-4 w-4 animate-spin" />
        Carregando roteamento de funil…
      </div>
    );
  }

  return (
    <div className="space-y-5 rounded-xl border border-[#2d2d2d] bg-[#131313] p-5 shadow-sm">
      <div>
        <h3 className="text-sm font-semibold text-white">Roteamento de Funil</h3>
        <p className="mt-1 text-xs text-gray-500">
          Mapeia etapas do CRM e/ou tags do lead para um especialista. Os dados são gravados em{" "}
          <code className="rounded bg-[#1f1f1f] px-1 text-[11px]">credenciais_canais.funnel_routing</code>.
        </p>
      </div>

      {loadError ? (
        <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">{loadError}</div>
      ) : null}

      <div className="grid gap-4 md:grid-cols-2">
        <div>
          <label className="mb-1 block text-xs text-gray-400">Prioridade de match</label>
          <select
            value={matchOrder}
            onChange={(e) => setMatchOrder(e.target.value)}
            className="w-full rounded-lg border border-[#2d2d2d] bg-[#181818] px-3 py-2 text-sm text-gray-200 outline-none focus:border-indigo-500"
          >
            <option value="tags_first">Tags primeiro, depois etapa</option>
            <option value="etapa_first">Etapa primeiro, depois tags</option>
          </select>
        </div>
        <div>
          <label className="mb-1 block text-xs text-gray-400">Especialista padrão (fallback)</label>
          <select
            value={defaultEspecialistaId}
            onChange={(e) => setDefaultEspecialistaId(e.target.value)}
            className="w-full rounded-lg border border-[#2d2d2d] bg-[#181818] px-3 py-2 text-sm text-gray-200 outline-none focus:border-indigo-500"
          >
            <option value="">— Nenhum —</option>
            {especialistas.map((esp) => (
              <option key={esp.id} value={esp.id}>
                {esp.nome || esp.id}
              </option>
            ))}
          </select>
        </div>
      </div>

      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-xs text-gray-500">Regras avaliadas em ordem de prioridade (campo Ordem).</p>
        <button
          type="button"
          onClick={adicionarRegra}
          className="inline-flex items-center gap-1 rounded-lg border border-[#2d2d2d] bg-[#1a1a1b] px-3 py-1.5 text-xs font-medium text-gray-200 hover:border-indigo-500/40"
        >
          <Plus size={14} />
          Adicionar regra
        </button>
      </div>

      <div className="space-y-4">
        {rules.length === 0 ? (
          <p className="text-xs text-gray-500">Nenhuma regra. Adicione pelo menos uma ou use só o especialista padrão.</p>
        ) : null}
        {rules.map((rule) => (
          <div key={rule._key} className="space-y-3 rounded-lg border border-[#2d2d2d] bg-[#181818] p-4">
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div className="grid flex-1 gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block text-[11px] uppercase tracking-wide text-gray-500">Especialista</label>
                  <select
                    value={rule.especialista_id}
                    onChange={(e) => atualizarRegra(rule._key, { especialista_id: e.target.value })}
                    className="w-full rounded-md border border-[#2d2d2d] bg-[#121212] px-2 py-1.5 text-sm text-gray-200 outline-none focus:border-indigo-500"
                  >
                    <option value="">— Selecione —</option>
                    {especialistas.map((esp) => (
                      <option key={esp.id} value={esp.id}>
                        {esp.nome || esp.id}
                      </option>
                    ))}
                  </select>
                </div>
                <div>
                  <label className="mb-1 block text-[11px] uppercase tracking-wide text-gray-500">Ordem (maior = mais prioritário)</label>
                  <input
                    type="number"
                    value={rule.ordem}
                    onChange={(e) => atualizarRegra(rule._key, { ordem: parseInt(e.target.value, 10) || 0 })}
                    className="w-full rounded-md border border-[#2d2d2d] bg-[#121212] px-2 py-1.5 text-sm text-gray-200 outline-none focus:border-indigo-500"
                  />
                </div>
              </div>
              <button
                type="button"
                onClick={() => removerRegra(rule._key)}
                className="rounded-md border border-red-500/30 p-2 text-red-300 hover:bg-red-500/10"
                title="Remover regra"
              >
                <Trash2 size={16} />
              </button>
            </div>

            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <label className="mb-1 block text-[11px] uppercase tracking-wide text-gray-500">Etapas do CRM</label>
                <div className="max-h-36 space-y-1 overflow-y-auto rounded-md border border-[#2d2d2d] bg-[#121212] p-2">
                  {etapas.length === 0 ? (
                    <span className="text-xs text-gray-500">Nenhuma etapa (carregue o funil CRM).</span>
                  ) : (
                    etapas.map((et) => {
                      const id = String(et.id);
                      const checked = rule.etapa_ids.includes(id);
                      return (
                        <label key={id} className="flex cursor-pointer flex-col gap-0.5 text-xs text-gray-300">
                          <span className="flex items-center gap-2">
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(e) =>
                                atualizarRegra(rule._key, {
                                  etapa_ids: toggleIdEmLista(rule.etapa_ids, id, e.target.checked),
                                })
                              }
                            />
                            <span>{mapaEtapas.get(id) || id}</span>
                          </span>
                          <span className="pl-6 font-mono text-[10px] text-gray-500" title="UUID gravado em funnel_routing">
                            {id}
                          </span>
                        </label>
                      );
                    })
                  )}
                </div>
              </div>
              <div>
                <label className="mb-1 block text-[11px] uppercase tracking-wide text-gray-500">Tags oficiais</label>
                <div className="max-h-36 space-y-1 overflow-y-auto rounded-md border border-[#2d2d2d] bg-[#121212] p-2">
                  {tags.length === 0 ? (
                    <span className="text-xs text-gray-500">Nenhuma tag oficial.</span>
                  ) : (
                    tags.map((tg) => {
                      const id = String(tg.id);
                      const checked = rule.tag_ids.includes(id);
                      return (
                        <label key={id} className="flex cursor-pointer flex-col gap-0.5 text-xs text-gray-300">
                          <span className="flex items-center gap-2">
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(e) =>
                                atualizarRegra(rule._key, {
                                  tag_ids: toggleIdEmLista(rule.tag_ids, id, e.target.checked),
                                })
                              }
                            />
                            <span>{mapaTags.get(id) || id}</span>
                          </span>
                          <span className="pl-6 font-mono text-[10px] text-gray-500" title="UUID gravado em funnel_routing">
                            {id}
                          </span>
                        </label>
                      );
                    })
                  )}
                </div>
              </div>
            </div>
            <p className="text-[11px] text-gray-500">
              Deixe etapas ou tags vazias para não restringir esse eixo (a regra aplica se o outro eixo casar, conforme a documentação do motor).
            </p>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={salvar}
          disabled={saving}
          className="inline-flex items-center gap-2 rounded-lg border border-indigo-500/40 bg-indigo-600 px-4 py-2 text-xs font-semibold text-white hover:bg-indigo-700 disabled:opacity-50"
        >
          {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : <Save size={14} />}
          {saving ? "Salvando…" : "Salvar roteamento"}
        </button>
        <button
          type="button"
          onClick={() => carregarFunnel()}
          disabled={saving}
          className="rounded-lg border border-[#2d2d2d] px-4 py-2 text-xs text-gray-300 hover:bg-[#1f1f1f] disabled:opacity-50"
        >
          Recarregar
        </button>
      </div>
    </div>
  );
}

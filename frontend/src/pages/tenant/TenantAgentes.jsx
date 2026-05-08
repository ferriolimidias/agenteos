import { useEffect, useState } from "react";
import { Bot, Sparkles } from "lucide-react";
import api from "../../services/api";
import { getActiveEmpresaId, getStoredUser } from "../../utils/auth";

function CardSkeleton() {
  return (
    <div className="animate-pulse rounded-xl border border-slate-200 bg-white p-6 shadow-sm">
      <div className="h-5 w-32 rounded-xl bg-slate-200" />
      <div className="mt-3 h-3 w-24 rounded-xl bg-slate-200" />
      <div className="mt-5 h-7 w-28 rounded-full bg-slate-200" />
    </div>
  );
}

export default function TenantAgentes() {
  const user = getStoredUser();
  const empresaId = getActiveEmpresaId();
  const hasUser = Boolean(user);
  const [loading, setLoading] = useState(true);
  const [agentes, setAgentes] = useState([]);
  const [erroCarregamento, setErroCarregamento] = useState("");

  useEffect(() => {
    const fetchAgentes = async () => {
      if (!empresaId || !hasUser) {
        setAgentes([]);
        setLoading(false);
        return;
      }

      try {
        setLoading(true);
        setErroCarregamento("");
        const res = await api.get(`/empresas/${empresaId}/agentes`);
        const data = res.data?.items || res.data || [];
        setAgentes(Array.isArray(data) ? data : []);
      } catch (error) {
        console.error("Erro ao buscar agentes:", error);
        setAgentes([]);
        setErroCarregamento("Não foi possível carregar seus agentes no momento.");
      } finally {
        setLoading(false);
      }
    };

    void fetchAgentes();
  }, [empresaId, hasUser]);

  if (!user) {
    return (
      <div className="rounded-xl border border-slate-200 bg-white p-6 text-sm text-slate-500 shadow-sm">
        Faça login novamente para visualizar seus agentes.
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold text-slate-900">Meus Agentes</h1>
        <p className="mt-1 text-sm text-slate-500">
          Visão da sua frota de IA ativa no atendimento.
        </p>
      </div>

      {loading ? (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          <CardSkeleton />
          <CardSkeleton />
          <CardSkeleton />
        </div>
      ) : agentes.length === 0 ? (
        <div className="flex items-center justify-center py-6">
          <div className="w-full max-w-xl rounded-xl border border-slate-200 bg-white p-8 text-center shadow-sm">
            <div className="mx-auto mb-4 flex h-12 w-12 items-center justify-center rounded-full bg-indigo-50 text-indigo-600">
              <Bot className="h-6 w-6" />
            </div>
            <h2 className="text-lg font-semibold text-slate-900">Nenhum agente configurado</h2>
            <p className="mt-2 text-sm text-slate-500">
              {erroCarregamento || "Quando seus agentes forem criados, eles aparecerão aqui para acompanhamento."}
            </p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="mt-5 inline-flex items-center justify-center rounded-lg bg-indigo-600 px-4 py-2 text-sm font-medium text-white shadow-sm transition-all hover:bg-indigo-700"
            >
              Tentar novamente
            </button>
          </div>
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {agentes.map((agente) => {
            const online = String(agente.status || "").toLowerCase() === "online";
            return (
              <div
                key={agente.id}
                className="rounded-xl border border-slate-200 bg-white p-6 shadow-sm transition-all hover:shadow-md"
              >
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="truncate text-lg font-semibold text-slate-900">{agente.nome}</h3>
                    <p className="mt-1 text-sm text-slate-500">{agente.funcao || "Especialista"}</p>
                  </div>
                  <Bot size={18} className="text-indigo-600" />
                </div>

                <div className="mt-5">
                  <span
                    className={`inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs font-semibold ${
                      online
                        ? "border-emerald-200 bg-emerald-50 text-emerald-700"
                        : "border-amber-200 bg-amber-50 text-amber-700"
                    }`}
                  >
                    <Sparkles size={12} />
                    Status: {online ? "Online" : "Offline"}
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}

import { useEffect, useState } from "react";
import { Bot, Sparkles } from "lucide-react";
import api from "../../services/api";
import { getActiveEmpresaId, getStoredUser } from "../../utils/auth";

function CardSkeleton() {
  return (
    <div className="rounded-2xl border border-[#2d2d2d] bg-[#1a1a1b] p-6 animate-pulse">
      <div className="h-5 w-32 rounded bg-[#2a2a2c]" />
      <div className="mt-3 h-3 w-24 rounded bg-[#2a2a2c]" />
      <div className="mt-4 h-6 w-20 rounded-full bg-[#2a2a2c]" />
    </div>
  );
}

export default function TenantAgentes() {
  const user = getStoredUser();
  const empresaId = getActiveEmpresaId();
  const [loading, setLoading] = useState(true);
  const [agentes, setAgentes] = useState([]);

  useEffect(() => {
    const carregarAgentes = async () => {
      if (!empresaId || !user) {
        setLoading(false);
        return;
      }
      setLoading(true);
      try {
        const res = await api.get(`/empresas/${empresaId}/agentes`);
        setAgentes(Array.isArray(res.data) ? res.data : []);
      } catch (err) {
        console.error("Erro ao carregar agentes do tenant:", err);
        setAgentes([]);
      } finally {
        setLoading(false);
      }
    };

    carregarAgentes();
  }, [empresaId, user]);

  if (!user) {
    return (
      <div className="rounded-2xl border border-[#2d2d2d] bg-[#1a1a1b] p-6 text-sm text-gray-300">
        Faça login novamente para visualizar seus agentes.
      </div>
    );
  }

  return (
    <div className="space-y-6 text-gray-100">
      <div>
        <h1 className="text-2xl font-semibold text-white">Meus Agentes</h1>
        <p className="mt-1 text-sm font-medium text-gray-400">
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
        <div className="rounded-2xl border border-[#2d2d2d] bg-[#1a1a1b] p-8 text-center text-sm text-gray-400">
          Nenhum agente cadastrado para este tenant.
        </div>
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
          {agentes.map((agente) => {
            const online = String(agente.status || "").toLowerCase() === "online";
            return (
              <div key={agente.id} className="rounded-2xl border border-[#2d2d2d] bg-[#1a1a1b] p-6">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h3 className="truncate text-lg font-semibold text-white">{agente.nome}</h3>
                    <p className="mt-1 text-sm font-medium text-gray-400">{agente.funcao || "Especialista"}</p>
                  </div>
                  <Bot size={18} className="text-indigo-300" />
                </div>

                <div className="mt-5">
                  <span
                    className={`inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs font-semibold ${
                      online
                        ? "border-emerald-500/40 bg-emerald-500/10 text-emerald-300"
                        : "border-amber-500/40 bg-amber-500/10 text-amber-300"
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

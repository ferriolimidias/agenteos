import { useState, useEffect } from "react";
import api from "../../services/api";
import { Users, UserPlus, MessageSquare, AlertCircle } from "lucide-react";
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, Cell, CartesianGrid } from "recharts";
import { getActiveEmpresaId, getStoredUser } from "../../utils/auth";

export default function Dashboard() {
  const [data, setData] = useState({
    total_leads: 0,
    total_faturamento_funil: 0,
    total_leads_convertidos_funil: 0,
    leads_por_etapa: [],
    aguardando_humano: 0,
    total_mensagens: 0
  });
  const [loading, setLoading] = useState(true);

  const user = getStoredUser();
  const empresaId = getActiveEmpresaId();

  useEffect(() => {
    const fetchDashboard = async () => {
      if (!user || !empresaId) {
        setLoading(false);
        return;
      }
      
      try {
        setLoading(true);
        const res = await api.get(`/empresas/${empresaId}/dashboard/stats`);
        setData(res.data);
      } catch (err) {
        console.error("Erro ao carregar dashboard:", err);
      } finally {
        setLoading(false);
      }
    };

    fetchDashboard();
  }, [empresaId]);

  if (!user) {
    return <div className="p-8 text-center text-gray-500">Faça login novamente.</div>;
  }

  const COLORS = ['#6366f1', '#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6'];
  const formatCurrency = (value) =>
    new Intl.NumberFormat("pt-BR", {
      style: "currency",
      currency: "BRL",
    }).format(Number(value || 0));

  return (
    <div className="space-y-6">
      <div className="mb-8">
        <h1 className="text-3xl font-bold text-gray-900 tracking-tight">Visão Geral</h1>
        <p className="text-gray-500 mt-1">Acompanhe o desempenho do seu agente e seus leads.</p>
      </div>

      {loading ? (
        <div className="grid grid-cols-1 md:grid-cols-4 gap-6">
          {[1, 2, 3, 4].map(i => (
            <div key={i} className="bg-white rounded-2xl p-6 shadow-sm border border-gray-100 animate-pulse">
              <div className="h-10 w-10 bg-gray-200 rounded-lg mb-4"></div>
              <div className="h-6 w-24 bg-gray-200 rounded mb-2"></div>
              <div className="h-8 w-16 bg-gray-200 rounded"></div>
            </div>
          ))}
        </div>
      ) : (
        <>
          <div className="grid grid-cols-1 md:grid-cols-4 gap-6">

            {/* Card Destaque: Faturamento do Funil */}
            <div className="bg-emerald-50 rounded-2xl p-6 shadow-sm border border-emerald-200 flex flex-col justify-between hover:shadow-md transition-shadow">
              <div className="flex items-center justify-between mb-4">
                <div className="w-12 h-12 bg-emerald-100 text-emerald-700 rounded-xl flex items-center justify-center">
                  <MessageSquare size={24} />
                </div>
              </div>
              <div>
                <p className="text-sm font-medium text-emerald-700 mb-1">Faturamento do Funil</p>
                <h3 className="text-3xl font-bold text-emerald-800 break-all">{formatCurrency(data.total_faturamento_funil)}</h3>
                {Number(data.total_leads_convertidos_funil || 0) > 0 ? (
                  <p className="mt-1 text-sm font-medium text-emerald-700">
                    {data.total_leads_convertidos_funil} {data.total_leads_convertidos_funil === 1 ? "venda" : "vendas"}
                  </p>
                ) : null}
              </div>
            </div>
            
            {/* Card 1: Total Leads */}
            <div className="bg-white rounded-2xl p-6 shadow-sm border border-gray-100 flex flex-col justify-between hover:shadow-md transition-shadow">
              <div className="flex items-center justify-between mb-4">
                <div className="w-12 h-12 bg-blue-50 text-blue-600 rounded-xl flex items-center justify-center">
                  <Users size={24} />
                </div>
              </div>
              <div>
                <p className="text-sm font-medium text-gray-500 mb-1">Total de Leads</p>
                <h3 className="text-3xl font-bold text-gray-900 break-all">{data.total_leads}</h3>
              </div>
            </div>

            {/* Card 2: Interações da IA */}
            <div className="bg-white rounded-2xl p-6 shadow-sm border border-gray-100 flex flex-col justify-between hover:shadow-md transition-shadow">
              <div className="flex items-center justify-between mb-4">
                <div className="w-12 h-12 bg-indigo-50 text-indigo-600 rounded-xl flex items-center justify-center">
                  <MessageSquare size={24} />
                </div>
              </div>
              <div>
                <p className="text-sm font-medium text-gray-500 mb-1">Interações da IA (Mensagens)</p>
                <h3 className="text-3xl font-bold text-gray-900 break-all">{data.total_mensagens}</h3>
              </div>
            </div>

            {/* Card 3: Alertas / Aguardando Humano */}
            <div className="bg-white rounded-2xl p-6 shadow-sm border border-gray-100 flex flex-col justify-between hover:shadow-md transition-shadow">
              <div className="flex items-center justify-between mb-4">
                <div className={`w-12 h-12 rounded-xl flex items-center justify-center ${data.aguardando_humano > 0 ? 'bg-red-50 text-red-600' : 'bg-green-50 text-green-600'}`}>
                  {data.aguardando_humano > 0 ? <AlertCircle size={24} /> : <UserPlus size={24} />}
                </div>
                {data.aguardando_humano > 0 && (
                  <span className="text-xs font-semibold text-red-600 bg-red-50 px-2 py-1 rounded-full animate-pulse">Ação Necessária</span>
                )}
              </div>
              <div>
                <p className="text-sm font-medium text-gray-500 mb-1">Aguardando Humano</p>
                <h3 className="text-3xl font-bold text-gray-900 break-all">{data.aguardando_humano}</h3>
              </div>
            </div>

          </div>

          {/* Gráficos */}
          <div className="mt-8 bg-white rounded-2xl p-6 shadow-sm border border-gray-100">
            <h3 className="text-lg font-semibold text-gray-900 mb-6">Leads por Etapa do Funil</h3>
            <div className="h-80">
              {data.leads_por_etapa.length > 0 ? (
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={data.leads_por_etapa} margin={{ top: 20, right: 30, left: 20, bottom: 5 }}>
                    <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#E5E7EB" />
                    <XAxis 
                      dataKey="name" 
                      axisLine={false} 
                      tickLine={false} 
                      tick={{ fill: '#6B7280', fontSize: 12 }} 
                      dy={10} 
                    />
                    <YAxis 
                      allowDecimals={false} 
                      axisLine={false} 
                      tickLine={false} 
                      tick={{ fill: '#6B7280', fontSize: 12 }} 
                      dx={-10} 
                    />
                    <Tooltip 
                      cursor={{fill: '#F3F4F6'}} 
                      contentStyle={{ borderRadius: '8px', border: 'none', boxShadow: '0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06)' }}
                    />
                    <Bar dataKey="value" radius={[6, 6, 0, 0]} maxBarSize={60}>
                      {
                        data.leads_por_etapa.map((entry, index) => (
                          <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} />
                        ))
                      }
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              ) : (
                <div className="h-full flex items-center justify-center text-gray-400">
                  Nenhum dado de funil para exibir.
                </div>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  );
}

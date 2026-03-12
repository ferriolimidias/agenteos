import { useState, useEffect } from "react";
import api from "../../services/api";
import { Calendar, Clock, Video, User, AlertCircle, CalendarDays } from "lucide-react";

export default function Agenda() {
  const [data, setData] = useState({ config: null, agendamentos: [] });
  const [loading, setLoading] = useState(true);

  const user = JSON.parse(localStorage.getItem("user"));
  const empresaId = user?.empresa_id || user?.id;

  const fetchAgenda = async () => {
    try {
      setLoading(true);
      const res = await api.get(`/empresas/${empresaId}/agenda`);
      setData(res.data);
    } catch (err) {
      console.error("Erro ao buscar dados da Agenda:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (empresaId) fetchAgenda();
    // eslint-disable-next-line
  }, [empresaId]);

  if (!user) return <div className="p-8 text-center text-gray-500">Faça login novamente.</div>;

  const { config, agendamentos } = data;

  const formatTime = (isoString) => {
    if (!isoString) return "";
    return new Date(isoString).toLocaleTimeString("pt-BR", { hour: "2-digit", minute: "2-digit" });
  };

  const formatDate = (isoString) => {
    if (!isoString) return "";
    return new Date(isoString).toLocaleDateString("pt-BR", { day: '2-digit', month: 'short', year: 'numeric' });
  };

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-gray-900 tracking-tight flex items-center gap-3">
          <Calendar className="text-blue-600" size={32} />
          Agenda Híbrida
        </h1>
        <p className="text-gray-500 mt-1">
          Acompanhe as reuniões e compromissos marcados automaticamente pelo seu Agente.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
        
        {/* Lado Esquerdo: Configurações */}
        <div className="lg:col-span-1 space-y-6">
          <div className="bg-white rounded-2xl shadow-sm border border-gray-100 p-6">
            <h2 className="text-lg font-semibold text-gray-800 mb-4 flex items-center gap-2">
              <Clock size={20} className="text-gray-400" /> 
              Configurações de Atendimento
            </h2>
            
            {!config ? (
              <div className="bg-yellow-50 text-yellow-800 p-4 rounded-xl flex items-start gap-3 border border-yellow-100">
                <AlertCircle size={20} className="text-yellow-600 flex-shrink-0 mt-0.5" />
                <div>
                  <h4 className="font-semibold text-sm">Não Configurada</h4>
                  <p className="text-xs mt-1">Sua agenda não possui horários definidos. A IA não está autorizada a realizar agendamentos no momento.</p>
                </div>
              </div>
            ) : (
              <div className="space-y-4">
                <div>
                  <span className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-2">Dias Ativos</span>
                  <div className="flex flex-wrap gap-2">
                    {['seg', 'ter', 'qua', 'qui', 'sex', 'sab', 'dom'].map(dia => (
                      <span 
                        key={dia} 
                        className={`px-3 py-1 rounded-full text-xs font-medium uppercase ${config.dias?.includes(dia) ? 'bg-blue-100 text-blue-700' : 'bg-gray-100 text-gray-400'}`}
                      >
                        {dia}
                      </span>
                    ))}
                  </div>
                </div>
                
                <div className="grid grid-cols-2 gap-4 pt-4 border-t border-gray-50">
                  <div>
                    <span className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">Início</span>
                    <span className="text-lg font-medium text-gray-900">{config.inicio}</span>
                  </div>
                  <div>
                    <span className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">Fim</span>
                    <span className="text-lg font-medium text-gray-900">{config.fim}</span>
                  </div>
                </div>
                
                <div className="pt-4 border-t border-gray-50">
                  <span className="block text-xs font-semibold text-gray-500 uppercase tracking-wider mb-1">Duração Padrão</span>
                  <span className="text-sm font-medium text-gray-900 bg-gray-100 px-3 py-1.5 rounded-lg inline-flex items-center gap-2">
                    <Clock size={14} className="text-gray-500"/>
                    {config.duracao_minutos} minutos
                  </span>
                </div>
              </div>
            )}
            
            <button className="w-full mt-6 text-sm text-blue-600 font-medium bg-blue-50 py-2.5 rounded-lg hover:bg-blue-100 transition-colors">
              Editar Preferências
            </button>
          </div>
        </div>

        {/* Lado Direito: Próximos Agendamentos */}
        <div className="lg:col-span-2">
          <div className="bg-white rounded-2xl shadow-sm border border-gray-100 overflow-hidden h-full flex flex-col">
            <div className="bg-gray-50/50 px-6 py-5 border-b border-gray-100 flex justify-between items-center">
              <h2 className="text-lg font-semibold text-gray-800 font-medium">Próximos Compromissos</h2>
              <span className="bg-green-100 text-green-700 text-xs font-bold px-3 py-1 rounded-full flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 bg-green-500 rounded-full"></span>
                {agendamentos.length} Agendados
              </span>
            </div>
            
            <div className="flex-1 p-6 overflow-y-auto max-h-[600px] custom-scrollbar">
              {loading ? (
                <div className="flex justify-center items-center h-40">
                  <div className="animate-pulse flex flex-col items-center">
                    <CalendarDays className="mb-3 text-gray-300" size={40} />
                    <span className="text-gray-400 font-medium text-sm">Carregando calendário...</span>
                  </div>
                </div>
              ) : agendamentos.length === 0 ? (
                <div className="text-center flex flex-col items-center justify-center text-gray-500 h-64">
                  <div className="w-16 h-16 bg-gray-50 rounded-2xl flex items-center justify-center mb-4 border border-gray-100">
                    <CalendarDays size={32} className="text-gray-300" />
                  </div>
                  <h3 className="text-lg font-medium text-gray-900 mb-1">Pauta Limpa</h3>
                  <p className="max-w-sm text-sm">Ainda não há reuniões agendadas pela IA para os próximos dias.</p>
                </div>
              ) : (
                <div className="space-y-4">
                  {agendamentos.map((ag) => (
                    <div key={ag.id} className="group relative flex gap-4 p-4 rounded-xl border border-gray-100 hover:border-blue-200 hover:shadow-md transition-all bg-white">
                      
                      {/* Data Vertical */}
                      <div className="w-16 flex-shrink-0 flex flex-col items-center justify-center bg-gray-50 rounded-lg py-2 border border-gray-100">
                        <span className="text-green-600 font-bold text-lg leading-none mb-1">
                          {new Date(ag.data_hora_inicio).getDate().toString().padStart(2, '0')}
                        </span>
                        <span className="text-xs uppercase text-gray-500 font-semibold tracking-wider">
                          {new Date(ag.data_hora_inicio).toLocaleString('pt-BR', { month: 'short' }).replace('.', '')}
                        </span>
                      </div>
                      
                      {/* Detalhes do Evento */}
                      <div className="flex-1 min-w-0 flex flex-col justify-center">
                        <div className="flex items-center justify-between mb-1">
                          <h4 className="font-semibold text-gray-900 text-lg flex items-center gap-2">
                            {ag.lead?.nome_contato}
                          </h4>
                          <span className="text-xs font-semibold bg-gray-100 text-gray-600 px-2 py-1 rounded-md">
                            {ag.status}
                          </span>
                        </div>
                        
                        <div className="flex items-center gap-4 text-sm text-gray-500">
                          <div className="flex items-center gap-1.5 font-medium text-gray-700 bg-gray-50/80 px-2 py-1 rounded border border-gray-100">
                            <Clock size={14} className="text-blue-500" />
                            {formatTime(ag.data_hora_inicio)} - {formatTime(ag.data_hora_fim)}
                          </div>
                          
                          <div className="flex items-center gap-1.5 text-gray-500">
                            <Video size={14} />
                            Google Meet
                          </div>
                        </div>
                      </div>

                      {/* Hover Actions */}
                      <div className="absolute top-4 right-4 opacity-0 group-hover:opacity-100 transition-opacity flex gap-2">
                         <button className="bg-white border border-gray-200 text-gray-600 hover:text-blue-600 p-1.5 rounded shadow-sm">
                           <User size={16} />
                         </button>
                      </div>

                    </div>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>

      </div>
    </div>
  );
}

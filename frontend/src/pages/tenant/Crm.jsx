import { useState, useEffect } from "react";
import api from "../../services/api";
import { Users, Plus, Phone, Clock, MessageSquare } from "lucide-react";

export default function Crm() {
  const [funil, setFunil] = useState(null);
  const [loading, setLoading] = useState(true);

  const user = JSON.parse(localStorage.getItem("user"));
  const empresaId = user?.empresa_id || user?.id;

  const fetchCrmData = async () => {
    try {
      setLoading(true);
      const res = await api.get(`/empresas/${empresaId}/crm`);
      setFunil(res.data);
    } catch (err) {
      console.error("Erro ao buscar dados do CRM:", err);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (empresaId) fetchCrmData();
    // eslint-disable-next-line
  }, [empresaId]);

  if (!user) return <div className="p-8 text-center text-gray-500">Faça login novamente.</div>;

  return (
    <div className="h-full flex flex-col space-y-6">
      {/* Header do CRM */}
      <div className="flex justify-between items-center flex-shrink-0">
        <div>
          <h1 className="text-3xl font-bold text-gray-900 tracking-tight flex items-center gap-3">
            <Users className="text-blue-600" size={32} />
            CRM Dinâmico
          </h1>
          <p className="text-gray-500 mt-1">
            {funil ? `Funil ativo: ${funil.funil_nome}` : "Gerencie seus leads capturados pela IA."}
          </p>
        </div>
        
        <button 
          onClick={() => alert("Criação manual de Lead em breve! A IA já faz isso automaticamente.")}
          className="bg-blue-600 hover:bg-blue-700 text-white px-4 py-2.5 rounded-lg flex items-center space-x-2 transition-colors font-medium shadow-sm"
        >
          <Plus size={20} />
          <span>Novo Lead</span>
        </button>
      </div>

      {loading ? (
        <div className="flex-1 flex justify-center items-center h-64">
          <div className="animate-pulse flex flex-col items-center">
            <Users className="mb-2 text-gray-300" size={48} />
            <span className="text-gray-400 font-medium">Carregando funil...</span>
          </div>
        </div>
      ) : !funil || !funil.etapas ? (
        <div className="flex-1 bg-white rounded-2xl border border-gray-100 flex items-center justify-center p-12">
          <p className="text-gray-500">Nenhum dado de CRM encontrado.</p>
        </div>
      ) : (
        /* Kanban Board Area - Scroll Horizontal */
        <div className="flex-1 overflow-x-auto pb-4 custom-scrollbar">
          <div className="flex space-x-6 min-w-max h-full">
            
            {funil.etapas.map((etapa) => (
              <div 
                key={etapa.id} 
                className="w-80 flex flex-col bg-gray-50/80 rounded-2xl border border-gray-200/60 overflow-hidden shadow-sm"
              >
                {/* Header da Coluna */}
                <div className="px-4 py-3 bg-gray-100/50 border-b border-gray-200/60 flex justify-between items-center cursor-move">
                  <h3 className="font-semibold text-gray-800 truncate">{etapa.nome}</h3>
                  <span className="bg-white border border-gray-200 text-gray-600 text-xs font-bold px-2 py-1 rounded-md shadow-sm">
                    {etapa.leads?.length || 0}
                  </span>
                </div>

                {/* Área de Cards (Leads) */}
                <div className="flex-1 p-3 overflow-y-auto space-y-3 custom-scrollbar min-h-[150px]">
                  {(!etapa.leads || etapa.leads.length === 0) ? (
                     <div className="h-full flex items-center justify-center text-sm font-medium text-gray-400/70 border-2 border-dashed border-gray-200 rounded-xl p-4 text-center">
                       Nenhum lead nesta etapa
                     </div>
                  ) : (
                    etapa.leads.map((lead) => (
                      <div 
                        key={lead.id}
                        className="bg-white p-4 rounded-xl shadow-[0_1px_3px_rgba(0,0,0,0.05)] border border-gray-100 hover:border-blue-300 hover:shadow-md transition-all cursor-pointer group"
                      >
                        <div className="flex justify-between items-start mb-2">
                          <h4 className="font-semibold text-gray-900 line-clamp-1">{lead.nome_contato}</h4>
                        </div>
                        
                        {lead.telefone && (
                          <div className="flex items-center text-sm text-gray-500 mb-3 gap-2">
                            <Phone size={14} className="text-green-600" />
                            <span className="font-medium text-gray-700">{lead.telefone}</span>
                          </div>
                        )}

                        {lead.historico_resumo && (
                          <p className="text-xs text-gray-500 line-clamp-2 mb-3 bg-gray-50 p-2 rounded-lg border border-gray-100">
                            {lead.historico_resumo}
                          </p>
                        )}
                        
                        <div className="flex items-center justify-between pt-3 border-t border-gray-50 mt-1">
                          <div className="flex items-center text-[11px] font-medium text-gray-400 gap-1 opacity-80">
                            <Clock size={12} />
                            {lead.criado_em ? new Date(lead.criado_em).toLocaleDateString('pt-BR') : 'Hoje'}
                          </div>
                          <button className="text-blue-600 bg-blue-50 p-1.5 rounded-lg opacity-0 group-hover:opacity-100 transition-opacity">
                            <MessageSquare size={14} />
                          </button>
                        </div>
                      </div>
                    ))
                  )}
                </div>

              </div>
            ))}
            
            {/* Coluna "Adicionar Etapa" (Visual placeholder) */}
            <div className="w-80 flex flex-col rounded-2xl border-2 border-dashed border-gray-200 opacity-50 hover:opacity-100 hover:bg-gray-50 hover:border-blue-300 transition-all cursor-pointer items-center justify-center min-h-[500px]">
              <div className="flex items-center gap-2 text-gray-500 font-medium pb-20">
                <Plus size={20} />
                Nova Etapa
              </div>
            </div>

          </div>
        </div>
      )}
    </div>
  );
}

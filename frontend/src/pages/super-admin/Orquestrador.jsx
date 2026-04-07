import { useState, useEffect } from "react";
import api from "../../services/api";
import { BrainCircuit, PenTool, Plus, X, Building, CheckCircle, RefreshCw, Pencil, Trash2 } from "lucide-react";

export default function Orquestrador() {
  const [empresas, setEmpresas] = useState([]);
  const [empresaSelecionadaId, setEmpresaSelecionadaId] = useState("");
  
  const [activeTab, setActiveTab] = useState("especialistas"); // 'especialistas' ou 'ferramentas'
  const [loading, setLoading] = useState(false);
  
  const [especialistas, setEspecialistas] = useState([]);
  const [ferramentas, setFerramentas] = useState([]);
  const [modelosDisponiveis, setModelosDisponiveis] = useState([]);

  // Modais e Edição
  const [showEspecialistaModal, setShowEspecialistaModal] = useState(false);
  const [showFerramentaModal, setShowFerramentaModal] = useState(false);
  const [editingEspecialistaId, setEditingEspecialistaId] = useState(null);
  const [editingFerramentaId, setEditingFerramentaId] = useState(null);
  const [submitting, setSubmitting] = useState(false);

  // Forms
  const [formEspecialista, setFormEspecialista] = useState({ nome: "", descricao_missao: "", prompt_sistema: "", modelo_ia: "gpt-4o-mini", usar_rag: false, usar_agenda: false, peso_prioridade: 1, fixo_no_roteador: false, ferramentas_ids: [] });
  const [formFerramenta, setFormFerramenta] = useState({ 
    nome_ferramenta: "", 
    descricao_ia: "",
    url: "",
    metodo: "GET",
    headers: "",
    payload: "",
    parameters_json: ""
  });

  // Carregar todas empresas no mount
  useEffect(() => {
    const fetchEmpresas = async () => {
      try {
        const res = await api.get("/empresas/");
        setEmpresas(res.data);
      } catch (err) {
        console.error("Erro ao buscar empresas", err);
      }
    };
    
    const fetchModelosDisponiveis = async () => {
      try {
        const res = await api.get("/admin/modelos-ia");
        setModelosDisponiveis(res.data);
      } catch (err) {
        console.error("Erro ao buscar modelos de IA:", err);
      }
    };
    
    fetchEmpresas();
    fetchModelosDisponiveis();
  }, []);

  // Carregar dados quando empresa_id mudar
  useEffect(() => {
    if (empresaSelecionadaId) {
      fetchEspecialistas();
      fetchFerramentas();
    } else {
      setEspecialistas([]);
      setFerramentas([]);
    }
  }, [empresaSelecionadaId]);

  const fetchEspecialistas = async () => {
    try {
      setLoading(true);
      const res = await api.get(`/admin/orquestrador/empresas/${empresaSelecionadaId}/especialistas`);
      setEspecialistas(res.data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  const fetchFerramentas = async () => {
    try {
      setLoading(true);
      const res = await api.get(`/admin/orquestrador/empresas/${empresaSelecionadaId}/ferramentas`);
      setFerramentas(res.data);
    } catch (err) {
      console.error(err);
    } finally {
      setLoading(false);
    }
  };

  // Submits
  const handleCreateOrUpdateEspecialista = async (e) => {
    e.preventDefault();
    try {
      setSubmitting(true);
      const pesoPrioridadeNumero = Number(formEspecialista.peso_prioridade);
      if (!Number.isFinite(pesoPrioridadeNumero) || pesoPrioridadeNumero < 1) {
        alert("O Peso de Prioridade deve ser um número maior ou igual a 1.");
        setSubmitting(false);
        return;
      }
      const payloadEspecialista = {
        ...formEspecialista,
        peso_prioridade: Math.max(1, Math.trunc(pesoPrioridadeNumero)),
      };
      if (editingEspecialistaId) {
        await api.put(`/admin/orquestrador/empresas/${empresaSelecionadaId}/especialistas/${editingEspecialistaId}`, payloadEspecialista);
        alert("Especialista atualizado com sucesso!");
      } else {
        await api.post(`/admin/orquestrador/empresas/${empresaSelecionadaId}/especialistas`, payloadEspecialista);
        alert("Especialista criado com sucesso!");
      }
      setShowEspecialistaModal(false);
      setEditingEspecialistaId(null);
      setFormEspecialista({ nome: "", descricao_missao: "", prompt_sistema: "", modelo_ia: "gpt-4o-mini", usar_rag: false, usar_agenda: false, peso_prioridade: 1, fixo_no_roteador: false, ferramentas_ids: [] });
      fetchEspecialistas();
    } catch (err) {
      alert("Erro ao salvar especialista");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteEspecialista = async (id) => {
    if (!window.confirm("Deseja realmente excluir este especialista?")) return;
    try {
      await api.delete(`/admin/orquestrador/empresas/${empresaSelecionadaId}/especialistas/${id}`);
      fetchEspecialistas();
    } catch (err) {
      alert("Erro ao excluir especialista");
    }
  };

  const openEditEspecialista = (esp) => {
    setFormEspecialista({
      nome: esp.nome,
      descricao_missao: esp.descricao_missao || "",
      prompt_sistema: esp.prompt_sistema,
      modelo_ia: esp.modelo_ia || "gpt-4o-mini",
      usar_rag: esp.usar_rag || false,
      usar_agenda: esp.usar_agenda || false,
      peso_prioridade: Number(esp.peso_prioridade || 1),
      fixo_no_roteador: esp.fixo_no_roteador || false,
      ferramentas_ids: esp.ferramentas_ids || []
    });
    setEditingEspecialistaId(esp.id);
    setShowEspecialistaModal(true);
  };

  const handleCreateOrUpdateFerramenta = async (e) => {
    e.preventDefault();
    try {
      setSubmitting(true);
      
      let parsedParams = {};
      try {
        if (formFerramenta.parameters_json && typeof formFerramenta.parameters_json === 'string') {
          parsedParams = JSON.parse(formFerramenta.parameters_json);
        } else if (typeof formFerramenta.parameters_json === 'object') {
            parsedParams = formFerramenta.parameters_json;
        }
      } catch (err) {
        alert("JSON de parâmetros inválido.");
        setSubmitting(false);
        return;
      }

      const payloadFormatado = {
        nome_ferramenta: formFerramenta.nome_ferramenta,
        descricao_ia: formFerramenta.descricao_ia,
        url: formFerramenta.url || null,
        metodo: formFerramenta.metodo || "GET",
        headers: formFerramenta.headers || null,
        payload: formFerramenta.payload || null,
        parameters_json: parsedParams
      };

      if (editingFerramentaId) {
        await api.put(`/admin/orquestrador/empresas/${empresaSelecionadaId}/ferramentas/${editingFerramentaId}`, payloadFormatado);
        alert("Ferramenta atualizada com sucesso!");
      } else {
        await api.post(`/admin/orquestrador/empresas/${empresaSelecionadaId}/ferramentas`, payloadFormatado);
        alert("Ferramenta criada com sucesso!");
      }
      
      setShowFerramentaModal(false);
      setEditingFerramentaId(null);
      setFormFerramenta({ 
        nome_ferramenta: "", 
        descricao_ia: "",
        url: "",
        metodo: "GET",
        headers: "",
        payload: "",
        parameters_json: ""
      });
      fetchFerramentas();
    } catch (err) {
      alert("Erro ao salvar ferramenta");
    } finally {
      setSubmitting(false);
    }
  };

  const handleDeleteFerramenta = async (id) => {
    if (!window.confirm("Deseja realmente excluir esta ferramenta?")) return;
    try {
      await api.delete(`/admin/orquestrador/empresas/${empresaSelecionadaId}/ferramentas/${id}`);
      fetchFerramentas();
    } catch (err) {
      alert("Erro ao excluir ferramenta");
    }
  };

  const openEditFerramenta = (tool) => {
    setFormFerramenta({
      nome_ferramenta: tool.nome_ferramenta,
      descricao_ia: tool.descricao_ia,
      url: tool.url || "",
      metodo: tool.metodo || "GET",
      headers: tool.headers || "",
      payload: tool.payload || "",
      parameters_json: tool.schema_parametros ? JSON.stringify(tool.schema_parametros, null, 2) : ""
    });
    setEditingFerramentaId(tool.id);
    setShowFerramentaModal(true);
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-col space-y-2">
        <h1 className="text-3xl font-bold text-white tracking-tight flex items-center gap-2">
          <BrainCircuit size={32} className="text-indigo-400" />
          Orquestrador de IA
        </h1>
        <p className="text-gray-400">Configure os Agentes Especialistas e Dinamic Tools por Tenant (Cliente).</p>
      </div>

      {/* Box de Seleção de Empresa */}
      <div className="bg-gray-900 border border-gray-800 rounded-xl p-6 shadow-sm">
        <label className="block text-sm font-medium text-gray-300 mb-2 flex items-center gap-2">
          <Building size={16} /> Selecione o Cliente (Tenant)
        </label>
        <select
          value={empresaSelecionadaId}
          onChange={(e) => setEmpresaSelecionadaId(e.target.value)}
          className="w-full bg-gray-950 border border-gray-800 rounded-lg py-3 px-4 text-white focus:ring-2 focus:ring-indigo-600 focus:border-indigo-600 outline-none transition-all"
        >
          <option value="">-- Selecione uma Empresa --</option>
          {empresas.map((emp) => (
            <option key={emp.id} value={emp.id}>{emp.nome_empresa} (ID: {emp.id.substring(0,8)}...)</option>
          ))}
        </select>
      </div>

      {empresaSelecionadaId && (
        <>
          {/* Navegação de Abas */}
          <div className="flex border-b border-gray-800">
            <button
              onClick={() => setActiveTab('especialistas')}
              className={`px-6 py-3 font-medium text-sm flex items-center gap-2 transition-colors ${activeTab === 'especialistas' ? 'border-b-2 border-indigo-500 text-indigo-400' : 'text-gray-400 hover:text-white'}`}
            >
              <BrainCircuit size={18} /> Especialistas
            </button>
            <button
              onClick={() => setActiveTab('ferramentas')}
              className={`px-6 py-3 font-medium text-sm flex items-center gap-2 transition-colors ${activeTab === 'ferramentas' ? 'border-b-2 border-indigo-500 text-indigo-400' : 'text-gray-400 hover:text-white'}`}
            >
              <PenTool size={18} /> Ferramentas (Tools)
            </button>
          </div>

          {/* Conteúdo Aba Especialistas */}
          {activeTab === 'especialistas' && (
            <div className="space-y-4">
              <div className="flex justify-between items-center">
                <h2 className="text-xl font-semibold text-white">Especialistas Cadastrados</h2>
                <button 
                  onClick={() => {
                    setEditingEspecialistaId(null);
                    setFormEspecialista({ nome: "", descricao_missao: "", prompt_sistema: "", modelo_ia: "gpt-4o-mini", usar_rag: false, usar_agenda: false, peso_prioridade: 1, fixo_no_roteador: false, ferramentas_ids: [] });
                    setShowEspecialistaModal(true);
                  }}
                  className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg flex items-center gap-2 text-sm font-medium transition-colors"
                >
                  <Plus size={16} /> Novo Especialista
                </button>
              </div>

              {loading ? (
                <div className="text-gray-400 py-8 text-center flex items-center justify-center gap-2"><RefreshCw className="animate-spin" size={18}/> Carregando...</div>
              ) : especialistas.length === 0 ? (
                <div className="bg-gray-900/50 border border-gray-800 rounded-xl p-8 text-center text-gray-400">
                  Nenhum especialista configurado para esta empresa.
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {especialistas.map((esp) => (
                    <div key={esp.id} className="bg-gray-900 border border-gray-800 rounded-xl p-5 hover:border-gray-700 transition-colors">
                      <div className="flex justify-between items-start mb-2 relative">
                        <div>
                          <h3 className="text-lg font-bold text-white flex items-center gap-2">
                            <CheckCircle size={16} className="text-green-500" /> {esp.nome}
                          </h3>
                          <span className="bg-gray-800 text-xs px-2 py-1 rounded-md text-gray-300 font-mono mt-1 inline-block">ID: {esp.id.substring(0,8)}</span>
                          <span className="bg-indigo-900/40 border border-indigo-700 text-xs px-2 py-1 rounded-md text-indigo-300 font-semibold mt-1 ml-2 inline-block">
                            Peso: {Number(esp.peso_prioridade || 1)}
                          </span>
                          {Boolean(esp.fixo_no_roteador) && (
                            <span className="bg-emerald-900/40 border border-emerald-700 text-xs px-2 py-1 rounded-md text-emerald-300 font-semibold mt-1 ml-2 inline-block">
                              Fixo no Roteador
                            </span>
                          )}
                        </div>
                        <div className="flex items-center gap-2">
                          <button onClick={() => openEditEspecialista(esp)} className="text-gray-400 hover:text-white p-1 rounded transition-colors" title="Editar">
                            <Pencil size={18} />
                          </button>
                          <button onClick={() => handleDeleteEspecialista(esp.id)} className="text-gray-400 hover:text-red-500 p-1 rounded transition-colors" title="Excluir">
                            <Trash2 size={18} />
                          </button>
                        </div>
                      </div>
                      <p className="text-sm text-indigo-300 font-medium mb-3">{esp.descricao_missao}</p>
                      <div className="bg-gray-950 rounded-lg p-3 text-xs text-gray-400 font-mono line-clamp-3 overflow-hidden">
                        {esp.prompt_sistema}
                      </div>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Conteúdo Aba Ferramentas */}
          {activeTab === 'ferramentas' && (
            <div className="space-y-4">
              <div className="flex justify-between items-center">
                <h2 className="text-xl font-semibold text-white">Dinamic Tools Cadastradas</h2>
                <button 
                  onClick={() => {
                    setEditingFerramentaId(null);
                    setFormFerramenta({ nome_ferramenta: "", descricao_ia: "", url: "", metodo: "GET", headers: "", payload: "", parameters_json: "" });
                    setShowFerramentaModal(true);
                  }}
                  className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg flex items-center gap-2 text-sm font-medium transition-colors"
                >
                  <Plus size={16} /> Nova Ferramenta
                </button>
              </div>

              {loading ? (
                <div className="text-gray-400 py-8 text-center flex items-center justify-center gap-2"><RefreshCw className="animate-spin" size={18}/> Carregando...</div>
              ) : ferramentas.length === 0 ? (
                <div className="bg-gray-900/50 border border-gray-800 rounded-xl p-8 text-center text-gray-400">
                  Nenhuma ferramenta configurada para esta empresa.
                </div>
              ) : (
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  {ferramentas.map((tool) => (
                    <div key={tool.id} className="bg-gray-900 border border-gray-800 rounded-xl p-5 hover:border-gray-700 transition-colors">
                      <div className="flex justify-between items-start mb-2 relative">
                        <h3 className="text-lg font-bold text-white flex items-center gap-2">
                          <PenTool size={16} className="text-blue-400" /> {tool.nome_ferramenta}
                        </h3>
                        <div className="flex items-center gap-2">
                          <button onClick={() => openEditFerramenta(tool)} className="text-gray-400 hover:text-white p-1 rounded transition-colors" title="Editar">
                            <Pencil size={18} />
                          </button>
                          <button onClick={() => handleDeleteFerramenta(tool.id)} className="text-gray-400 hover:text-red-500 p-1 rounded transition-colors" title="Excluir">
                            <Trash2 size={18} />
                          </button>
                        </div>
                      </div>
                      <p className="text-sm text-gray-300 bg-gray-950 p-3 rounded-lg border border-gray-800">{tool.descricao_ia}</p>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </>
      )}

      {/* Modal Novo Especialista */}
      {showEspecialistaModal && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center p-4 z-50">
          <div className="bg-gray-900 rounded-2xl border border-gray-800 shadow-xl w-[95%] md:w-full max-w-xl mx-auto overflow-hidden max-h-[90vh] flex flex-col">
            <div className="px-6 py-4 border-b border-gray-800 flex justify-between items-center bg-gray-950">
              <h2 className="text-xl font-bold text-white flex items-center gap-2"><BrainCircuit size={20}/> {editingEspecialistaId ? "Editar Especialista" : "Novo Especialista"}</h2>
              <button onClick={() => setShowEspecialistaModal(false)} className="text-gray-400 hover:text-white transition-colors"><X size={20} /></button>
            </div>
            <div className="overflow-y-auto custom-scrollbar">
              <form onSubmit={handleCreateOrUpdateEspecialista} className="p-6 sm:px-8 space-y-4">
              <div>
                <label className="block text-sm font-semibold text-gray-300 mb-1">Nome do Agente</label>
                <input required type="text" placeholder="Ex: Especialista em Agendamentos"
                  className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 outline-none"
                  value={formEspecialista.nome} onChange={(e) => setFormEspecialista({...formEspecialista, nome: e.target.value})}
                />
              </div>
              <div>
                <label className="block text-sm font-semibold text-gray-300 mb-1">Missão (Descrição Curta)</label>
                <input required type="text" placeholder="Ex: Marca consultas na agenda local"
                  className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 outline-none"
                  value={formEspecialista.descricao_missao} onChange={(e) => setFormEspecialista({...formEspecialista, descricao_missao: e.target.value})}
                />
              </div>
              <div>
                <label className="block text-sm font-semibold text-gray-300 mb-1">Prompt de Sistema (Persona)</label>
                <textarea required rows={4} placeholder="Você é responsável por..."
                  className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 outline-none resize-none"
                  value={formEspecialista.prompt_sistema} onChange={(e) => setFormEspecialista({...formEspecialista, prompt_sistema: e.target.value})}
                />
              </div>

              <div>
                <label className="block text-sm font-semibold text-gray-300 mb-1">Modelo de IA do Especialista</label>
                <select
                  value={formEspecialista.modelo_ia}
                  onChange={(e) => setFormEspecialista({...formEspecialista, modelo_ia: e.target.value})}
                  className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 outline-none transition-all"
                >
                  {modelosDisponiveis.map(m => <option key={m} value={m}>{m}</option>)}
                  {!modelosDisponiveis.includes("gpt-4o-mini") && <option value="gpt-4o-mini">gpt-4o-mini</option>}
                </select>
              </div>

              <div>
                <label className="block text-sm font-semibold text-gray-300 mb-1">Peso de Prioridade</label>
                <input
                  type="number"
                  min={1}
                  step={1}
                  className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 outline-none"
                  value={formEspecialista.peso_prioridade}
                  onChange={(e) => setFormEspecialista({...formEspecialista, peso_prioridade: e.target.value})}
                />
                <p className="text-xs text-indigo-400 mt-1">
                  Define a prioridade deste agente na fila. Agentes com maior peso agem primeiro.
                </p>
              </div>

              <div>
                <label
                  className="flex items-center gap-3 p-3 bg-gray-950 border border-gray-800 rounded-lg cursor-pointer hover:border-indigo-500 transition-colors"
                  title="Se ativado, este agente será sempre enviado para a análise do Maestro no início do atendimento, independentemente do assunto."
                >
                  <input
                    type="checkbox"
                    className="w-4 h-4 rounded text-indigo-600 focus:ring-indigo-500 bg-gray-900 border-gray-700"
                    checked={formEspecialista.fixo_no_roteador}
                    onChange={(e) => setFormEspecialista({...formEspecialista, fixo_no_roteador: e.target.checked})}
                  />
                  <div>
                    <p className="text-sm font-semibold text-white">Agente Fixo no Roteador (Acionamento Obrigatório)</p>
                    <p className="text-xs text-gray-400 mt-0.5">
                      Se ativado, este agente será sempre enviado para a análise do Maestro no início do atendimento, independentemente do assunto.
                    </p>
                  </div>
                </label>
              </div>

              {/* RAG Toggle */}
              <div className="pt-4 border-t border-gray-800 mt-6">
                <h3 className="text-sm font-bold text-white mb-3">Conhecimento da IA</h3>
                <label className="flex items-center gap-3 p-3 bg-gray-950 border border-gray-800 rounded-lg cursor-pointer hover:border-indigo-500 transition-colors">
                  <input
                    type="checkbox"
                    className="w-4 h-4 rounded text-indigo-600 focus:ring-indigo-500 bg-gray-900 border-gray-700"
                    checked={formEspecialista.usar_rag}
                    onChange={(e) => setFormEspecialista({...formEspecialista, usar_rag: e.target.checked})}
                  />
                  <div>
                    <p className="text-sm font-semibold text-white">Habilitar Busca em Documentos (RAG)</p>
                    <p className="text-xs text-gray-400 mt-0.5">Se marcado, o Agente pesquisará na base de conhecimento (PDF, URL, Textos) antes de responder ao cliente.</p>
                  </div>
                </label>
              </div>

              {/* Agenda Nativa Toggle */}
              <div className="pt-2">
                <label className="flex items-center gap-3 p-3 bg-gray-950 border border-gray-800 rounded-lg cursor-pointer hover:border-indigo-500 transition-colors">
                  <input
                    type="checkbox"
                    className="w-4 h-4 rounded text-indigo-600 focus:ring-indigo-500 bg-gray-900 border-gray-700"
                    checked={formEspecialista.usar_agenda}
                    onChange={(e) => setFormEspecialista({...formEspecialista, usar_agenda: e.target.checked})}
                  />
                  <div>
                    <p className="text-sm font-semibold text-white">Ativar Agendamento Nativo</p>
                    <p className="text-xs text-gray-400 mt-0.5">Permite que este especialista acesse e gerencie a agenda do sistema (consulte, marque e cancele horários).</p>
                  </div>
                </label>
              </div>

              {/* Ferramentas do Especialista */}
              {ferramentas.length > 0 && (
                <div className="pt-4 border-t border-gray-800 mt-6">
                  <h3 className="text-sm font-bold text-white mb-3">Ferramentas Vinculadas (Opcional)</h3>
                  <div className="space-y-2 max-h-48 overflow-y-auto custom-scrollbar">
                    {ferramentas.map((tool) => (
                      <label key={tool.id} className="flex items-start gap-3 p-3 bg-gray-950 border border-gray-800 rounded-lg cursor-pointer hover:border-indigo-500 transition-colors">
                        <input
                          type="checkbox"
                          className="mt-1 w-4 h-4 rounded text-indigo-600 focus:ring-indigo-500 bg-gray-900 border-gray-700"
                          checked={formEspecialista.ferramentas_ids?.includes(tool.id)}
                          onChange={(e) => {
                            const isChecked = e.target.checked;
                            setFormEspecialista(prev => {
                              const novasIds = isChecked 
                                ? [...(prev.ferramentas_ids || []), tool.id]
                                : (prev.ferramentas_ids || []).filter(id => id !== tool.id);
                              return { ...prev, ferramentas_ids: novasIds };
                            });
                          }}
                        />
                        <div>
                          <p className="text-sm font-semibold text-white">{tool.nome_ferramenta}</p>
                          <p className="text-xs text-gray-400 mt-0.5 line-clamp-1">{tool.descricao_ia}</p>
                        </div>
                      </label>
                    ))}
                  </div>
                  <p className="text-xs text-indigo-400 mt-2">Dica: O Especialista terá acesso apenas às ferramentas selecionadas.</p>
                </div>
              )}

              <div className="mt-8 flex justify-end gap-3 pt-4 border-t border-gray-800">
                <button type="button" onClick={() => setShowEspecialistaModal(false)} className="px-4 py-2 text-gray-400 hover:text-white">Cancelar</button>
                <button type="submit" disabled={submitting} className="bg-indigo-600 hover:bg-indigo-700 text-white px-6 py-2 rounded-lg font-medium transition-colors">
                  {submitting ? "Salvando..." : "Salvar Especialista"}
                </button>
              </div>
            </form>
            </div>
          </div>
        </div>
      )}

      {/* Modal Nova Ferramenta */}
      {showFerramentaModal && (
        <div className="fixed inset-0 bg-black/70 flex items-center justify-center p-4 z-50">
          <div className="bg-gray-900 rounded-2xl border border-gray-800 shadow-xl w-full max-w-2xl overflow-hidden max-h-[90vh] flex flex-col">
            <div className="px-6 py-4 border-b border-gray-800 flex justify-between items-center bg-gray-950">
              <h2 className="text-xl font-bold text-white flex items-center gap-2"><PenTool size={20}/> {editingFerramentaId ? "Editar Ferramenta" : "Nova Ferramenta"}</h2>
              <button onClick={() => setShowFerramentaModal(false)} className="text-gray-400 hover:text-white transition-colors"><X size={20} /></button>
            </div>
            <div className="overflow-y-auto custom-scrollbar">
              <form onSubmit={handleCreateOrUpdateFerramenta} className="p-6 space-y-4">
                <div>
                <label className="block text-sm font-semibold text-gray-300 mb-1">Nome da Ferramenta</label>
                <input required type="text" placeholder="Ex: buscar_tabela_precos"
                  className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 outline-none"
                  value={formFerramenta.nome_ferramenta} onChange={(e) => setFormFerramenta({...formFerramenta, nome_ferramenta: e.target.value})}
                />
                <p className="text-xs text-gray-500 mt-1">Use snake_case, sem espaços.</p>
              </div>
              <div>
                <label className="block text-sm font-semibold text-gray-300 mb-1">Descrição para a IA</label>
                <textarea required rows={4} placeholder="Use esta ferramenta quando o cliente perguntar os preços dos tratamentos..."
                  className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 outline-none resize-none bg-indigo-900/10 border-indigo-500/30"
                  value={formFerramenta.descricao_ia} onChange={(e) => setFormFerramenta({...formFerramenta, descricao_ia: e.target.value})}
                />
                <p className="text-xs text-indigo-400 mt-1 font-medium">⚠️ Descreva detalhadamente para que serve esta ferramenta, pois a IA usará essa explicação para decidir quando ativá-la.</p>
              </div>

              {/* Configuração Técnica */}
              <div className="pt-4 border-t border-gray-800 mt-6">
                <h3 className="text-lg font-bold text-white mb-4">Configuração Técnica (API)</h3>
                
                <div className="mb-4">
                  <label className="block text-sm font-semibold text-gray-300 mb-1">Parâmetros da Tool (JSON Schema)</label>
                  <textarea rows={3} placeholder='{"id_pedido": "string", "cpf": "string"}'
                    className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 outline-none font-mono text-sm resize-none"
                    value={formFerramenta.parameters_json} onChange={(e) => setFormFerramenta({...formFerramenta, parameters_json: e.target.value})}
                  />
                  <p className="text-xs text-indigo-400 mt-1">A IA será forçada a extrair estes dados da conversa antes de chamar a API.</p>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
                  <div className="col-span-2">
                    <label className="block text-sm font-semibold text-gray-300 mb-1">URL da API</label>
                    <input type="url" placeholder="https://api.exemplo.com/v1/dados"
                      className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 outline-none"
                      value={formFerramenta.url} onChange={(e) => setFormFerramenta({...formFerramenta, url: e.target.value})}
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-semibold text-gray-300 mb-1">Método HTTP</label>
                    <select
                      className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 outline-none"
                      value={formFerramenta.metodo} onChange={(e) => setFormFerramenta({...formFerramenta, metodo: e.target.value})}
                    >
                      <option value="GET">GET</option>
                      <option value="POST">POST</option>
                      <option value="PUT">PUT</option>
                      <option value="DELETE">DELETE</option>
                    </select>
                  </div>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                  <div>
                    <label className="block text-sm font-semibold text-gray-300 mb-1">Headers (JSON Opcional)</label>
                    <textarea rows={3} placeholder='{"Authorization": "Bearer token"}'
                      className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 outline-none font-mono text-sm resize-none"
                      value={formFerramenta.headers} onChange={(e) => setFormFerramenta({...formFerramenta, headers: e.target.value})}
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-semibold text-gray-300 mb-1">Payload (JSON com {"{{var}}"})</label>
                    <textarea rows={3} placeholder='{"email": "{{email_cliente}}"}'
                      className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 outline-none font-mono text-sm resize-none"
                      value={formFerramenta.payload} onChange={(e) => setFormFerramenta({...formFerramenta, payload: e.target.value})}
                    />
                    <p className="text-xs text-indigo-400 mt-1">Variáveis entre double brackets obrigam a IA a preenchê-las.</p>
                  </div>
                </div>
              </div>

              <div className="mt-8 flex justify-end gap-3 pt-4 border-t border-gray-800">
                <button type="button" onClick={() => setShowFerramentaModal(false)} className="px-4 py-2 text-gray-400 hover:text-white">Cancelar</button>
                <button type="submit" disabled={submitting} className="bg-indigo-600 hover:bg-indigo-700 text-white px-6 py-2 rounded-lg font-medium transition-colors">
                  {submitting ? "Salvando..." : "Salvar Ferramenta"}
                </button>
              </div>
            </form>
            </div>
          </div>
        </div>
      )}

    </div>
  );
}

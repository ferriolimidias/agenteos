import { useState, useEffect } from "react";
import api from "../../services/api";
import { Plus, Building, MapPin, X, User, LogIn, KeyRound, Settings, RefreshCw, Pencil, Trash, Brain, UserCheck, FileText } from "lucide-react";
import RAGManager from "../../components/RAGManager";
import EmpresaConexoesManager from "../../components/EmpresaConexoesManager";
import { clearImpersonation } from "../../utils/auth";

import { useNavigate } from "react-router-dom";

export default function Empresas() {
  const navigate = useNavigate();
  const [empresas, setEmpresas] = useState([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [showRagModal, setShowRagModal] = useState(false);
  
  // Modal de Credenciais
  const [showCredenciaisModal, setShowCredenciaisModal] = useState(false);
  const [empresaSelecionada, setEmpresaSelecionada] = useState(null);
  const [activeIntegracaoTab, setActiveIntegracaoTab] = useState("conexoes");
  const [credenciais, setCredenciais] = useState({
    evolution_url: "",
    evolution_apikey: "",
    evolution_instance: "",
    openai_api_key: ""
  });
  const [savingCredenciais, setSavingCredenciais] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState(null);

  // Form State
  const [formData, setFormData] = useState({
    nome_empresa: "",
    area_atuacao: "",
    logo_url: "",
    admin_nome: "",
    admin_email: "",
    admin_senha: ""
  });

  // Edit State
  const [showEditModal, setShowEditModal] = useState(false);
  const [empresaEdit, setEmpresaEdit] = useState(null);
  const [editFormData, setEditFormData] = useState({
    nome_empresa: "",
    area_atuacao: "",
    logo_url: ""
  });

  // Config IA State
  const [showConfigIAModal, setShowConfigIAModal] = useState(false);
  const [configIAData, setConfigIAData] = useState({
    modelo_ia: "gpt-4o-mini",
    modelo_roteador: "gpt-4o-mini",
    followup_ativo: false,
    followup_espera_nivel_1_minutos: 20,
    followup_espera_nivel_2_minutos: 10,
    limite_certeza: 0.65,
    limite_duvida: 0.45,
    max_agentes_desempate: 3,
  });
  const [loadingConfigIA, setLoadingConfigIA] = useState(false);
  const [savingConfigIA, setSavingConfigIA] = useState(false);
  const [modelosDisponiveis, setModelosDisponiveis] = useState([]);

  const fetchEmpresas = async () => {
    try {
      setLoading(true);
      const res = await api.get("/empresas/");
      setEmpresas(res.data);
    } catch (err) {
      console.error("Erro ao buscar empresas:", err);
    } finally {
      setLoading(false);
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

  useEffect(() => {
    fetchEmpresas();
    fetchModelosDisponiveis();
  }, []);

  const handleChange = (e) => {
    const { name, value } = e.target;
    setFormData(prev => ({ ...prev, [name]: value }));
  };

  const handleLogoUpload = (e) => {
    const file = e.target.files[0];
    if (file) {
      const reader = new FileReader();
      reader.onloadend = () => {
        setFormData({ ...formData, logo_url: reader.result });
      };
      reader.readAsDataURL(file);
    }
  };

  const handleEditChange = (e) => {
    const { name, value } = e.target;
    setEditFormData(prev => ({ ...prev, [name]: value }));
  };

  const handleEditLogoUpload = (e) => {
    const file = e.target.files[0];
    if (file) {
      const reader = new FileReader();
      reader.onloadend = () => {
        setEditFormData({ ...editFormData, logo_url: reader.result });
      };
      reader.readAsDataURL(file);
    }
  };

  const openEditModal = (emp) => {
    setEmpresaEdit(emp);
    setEditFormData({
      nome_empresa: emp.nome_empresa || "",
      area_atuacao: emp.area_atuacao || "",
      logo_url: emp.logo_url || ""
    });
    setShowEditModal(true);
  };

  const handleEditSubmit = async (e) => {
    e.preventDefault();
    setSubmitting(true);
    try {
      await api.put(`/empresas/${empresaEdit.id}`, editFormData);
      setShowEditModal(false);
      fetchEmpresas();
    } catch (err) {
      alert("Erro ao atualizar cliente.");
      console.error(err);
    } finally {
      setSubmitting(false);
    }
  };

  const handleDelete = async (empId) => {
    if (window.confirm("Tem certeza? Isso apagará todos os dados deste cliente!")) {
      try {
        await api.delete(`/empresas/${empId}`);
        fetchEmpresas();
      } catch (err) {
        alert("Erro ao excluir cliente.");
        console.error(err);
      }
    }
  };

  const handleSubmit = async (e) => {
    e.preventDefault();
    setError(null);
    setSubmitting(true);

    try {
      await api.post("/empresas/setup", formData);
      setShowModal(false);
      setFormData({
        nome_empresa: "",
        area_atuacao: "",
        logo_url: "",
        admin_nome: "",
        admin_email: "",
        admin_senha: ""
      });
      fetchEmpresas(); // Recarrega a lista
    } catch (err) {
      console.error("Erro no setup:", err);
      // Extrai detalhe exato do backend se houver (ex: Erro banco de dados: ...)
      if (err.response && err.response.data && err.response.data.detail) {
        setError(err.response.data.detail);
      } else {
        setError("Erro de conexão ao servidor. Tente novamente.");
      }
    } finally {
      setSubmitting(false);
    }
  };

  const openCredenciaisModal = (emp) => {
    setEmpresaSelecionada(emp);
    setActiveIntegracaoTab("conexoes");
    // Tentar pré-popular se houver (o mock endpoint atual não traz as credenciais pro front de cara, mas se no futuro vier populado)
    setCredenciais({
      evolution_url: emp.credenciais_canais?.evolution_url || "",
      evolution_apikey: emp.credenciais_canais?.evolution_apikey || "",
      evolution_instance: emp.credenciais_canais?.evolution_instance || "",
      openai_api_key: emp.credenciais_canais?.openai_api_key || ""
    });
    setShowCredenciaisModal(true);
  };

  const handleConexaoDisparoUpdated = (nextConfig) => {
    setEmpresaSelecionada((prev) => (
      prev ? { ...prev, ...nextConfig } : prev
    ));
    setEmpresas((prev) => prev.map((empresa) => (
      empresa.id === empresaSelecionada?.id
        ? { ...empresa, ...nextConfig }
        : empresa
    )));
  };

  const handleSaveCredenciais = async (e) => {
    e.preventDefault();
    try {
      setSavingCredenciais(true);
      await api.put(`/empresas/${empresaSelecionada.id}/credenciais`, credenciais);
      alert("Credenciais salvas com sucesso!");
      setShowCredenciaisModal(false);
      fetchEmpresas();
    } catch (err) {
      alert("Erro ao salvar credenciais");
      console.error(err);
    } finally {
      setSavingCredenciais(false);
    }
  };

  const openConfigIAModal = async (emp) => {
    setEmpresaSelecionada(emp);
    setShowConfigIAModal(true);
    setLoadingConfigIA(true);
    try {
      const res = await api.get(`/empresas/${emp.id}/ia-config`);
      setConfigIAData({
        ia_instrucoes_personalizadas: res.data.ia_instrucoes_personalizadas || "",
        ia_tom_voz: res.data.ia_tom_voz || "",
        nome_agente: res.data.nome_agente || "",
        mensagem_saudacao: res.data.mensagem_saudacao || "",
        modelo_ia: res.data.modelo_ia || "gpt-4o-mini",
        modelo_roteador: res.data.modelo_roteador || "gpt-4o-mini",
        followup_ativo: res.data.followup_ativo || false,
        followup_espera_nivel_1_minutos: res.data.followup_espera_nivel_1_minutos || 20,
        followup_espera_nivel_2_minutos: res.data.followup_espera_nivel_2_minutos || 10,
        limite_certeza: typeof res.data.limite_certeza === "number" ? res.data.limite_certeza : 0.65,
        limite_duvida: typeof res.data.limite_duvida === "number" ? res.data.limite_duvida : 0.45,
        max_agentes_desempate: typeof res.data.max_agentes_desempate === "number" ? res.data.max_agentes_desempate : 3,
      });
    } catch (err) {
      console.error(err);
      alert("Erro ao carregar configurações do Agente.");
      setShowConfigIAModal(false);
    } finally {
      setLoadingConfigIA(false);
    }
  };

  const handleSaveConfigIA = async (e) => {
    e.preventDefault();
    setSavingConfigIA(true);
    try {
      await api.put(`/empresas/${empresaSelecionada.id}/ia-config`, configIAData);
      alert("Configuração do Agente salva com sucesso!");
      setShowConfigIAModal(false);
    } catch (err) {
      console.error(err);
      alert("Erro ao salvar a configuração do Agente.");
    } finally {
      setSavingConfigIA(false);
    }
  };

  const openRagModal = (emp) => {
    setEmpresaSelecionada(emp);
    setShowRagModal(true);
  };

  const handleImpersonate = async (emp) => {
    try {
      const res = await api.post(`/auth/impersonate/${emp.id}`);
      const data = res.data;
      const currentUser = localStorage.getItem("user");
      const currentToken = localStorage.getItem("token");

      // Mantém a identidade autenticada original e troca apenas o tenant ativo.
      clearImpersonation();
      if (currentUser) {
        localStorage.setItem("original_user", currentUser);
      }
      if (currentToken && currentToken !== "null" && currentToken !== "undefined") {
        localStorage.setItem("original_token", currentToken);
      }
      localStorage.setItem("impersonating", "true");
      localStorage.setItem("impersonating_empresa", emp.nome_empresa);
      localStorage.setItem("impersonated_empresa_id", emp.id);
      if (data?.usuario?.id) {
        localStorage.setItem("impersonated_user_id", data.usuario.id);
      }

      window.location.href = "/painel"; // Full reload to clear states and trigger layout update
    } catch (err) {
      console.error(err);
      alert(err.response?.data?.detail || "Erro ao tentar personificar cliente.");
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <div>
          <h1 className="text-3xl font-bold text-white tracking-tight">Gestão de Clientes</h1>
          <p className="text-gray-400 mt-1">Gerencie as empresas (tenants) e seus acessos ao sistema.</p>
        </div>
        <button 
          onClick={() => setShowModal(true)}
          className="bg-indigo-600 hover:bg-indigo-700 text-white px-4 py-2 rounded-lg flex items-center space-x-2 transition-colors font-medium shadow-sm"
        >
          <Plus size={20} />
          <span>Novo Cliente</span>
        </button>
      </div>

      <div className="bg-gray-950 border border-gray-800 rounded-xl overflow-hidden shadow-sm">
        <div className="overflow-x-auto">
          <table className="w-full text-left text-sm whitespace-nowrap">
            <thead className="bg-gray-900 text-gray-400 border-b border-gray-800 uppercase tracking-wider text-xs font-semibold">
              <tr>
                <th className="px-6 py-4">Nome da Empresa</th>
                <th className="px-6 py-4">Área de Atuação</th>
                <th className="px-6 py-4 text-right">ID do Tenant</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-800 text-gray-200">
              {loading ? (
                <tr>
                  <td colSpan="3" className="px-6 py-8 text-center text-gray-500">Carregando empresas...</td>
                </tr>
              ) : empresas.length === 0 ? (
                <tr>
                  <td colSpan="3" className="px-6 py-8 text-center text-gray-500">Nenhum cliente cadastrado.</td>
                </tr>
              ) : (
                empresas.map((emp) => (
                  <tr key={emp.id} className="hover:bg-gray-800/50 transition-colors">
                    <td className="px-6 py-4 font-medium flex items-center space-x-3">
                      {emp.logo_url ? (
                        <img
                          src={emp.logo_url}
                          alt={`Logo de ${emp.nome_empresa}`}
                          className="w-8 h-8 rounded-md object-contain"
                        />
                      ) : (
                        <div className="w-8 h-8 rounded-md bg-indigo-900/50 text-indigo-400 flex items-center justify-center">
                          <Building size={16} />
                        </div>
                      )}
                      <span>{emp.nome_empresa}</span>
                    </td>
                    <td className="px-6 py-4 text-gray-400">
                      <div className="flex items-center space-x-2">
                        {emp.area_atuacao ? (
                          <>
                            <MapPin size={14} />
                            <span>{emp.area_atuacao}</span>
                          </>
                        ) : (
                          <span className="text-gray-600 italic">Não informada</span>
                        )}
                      </div>
                    </td>
                    <td className="px-6 py-4 text-right text-xs font-mono text-gray-500">
                      <div className="flex items-center justify-end space-x-4">
                        <span>{emp.id}</span>
                        <button 
                          onClick={() => handleImpersonate(emp)}
                          className="text-gray-400 hover:text-emerald-500 transition-colors p-2 hover:bg-emerald-50/10 rounded-lg inline-flex items-center gap-2 border border-gray-800"
                          title="Acessar Painel (Impersonate)"
                        >
                          <UserCheck size={16} />
                        </button>
                        <button 
                          onClick={() => openConfigIAModal(emp)}
                          className="text-gray-400 hover:text-purple-400 transition-colors p-2 hover:bg-purple-50/10 rounded-lg inline-flex items-center gap-2 border border-gray-800"
                          title="Configuração do Agente"
                        >
                          <Brain size={16} />
                        </button>
                        <button 
                          onClick={() => openRagModal(emp)}
                          className="text-gray-400 hover:text-orange-400 transition-colors p-2 hover:bg-orange-50/10 rounded-lg inline-flex items-center gap-2 border border-gray-800"
                          title="Treinamento RAG"
                        >
                          <FileText size={16} />
                        </button>
                        <button 
                          onClick={() => openCredenciaisModal(emp)}
                          className="text-gray-400 hover:text-blue-600 transition-colors p-2 hover:bg-blue-50/10 rounded-lg inline-flex items-center gap-2 border border-gray-800"
                          title="Configurar Integrações"
                        >
                          <Settings size={16} />
                        </button>
                        <button 
                          onClick={() => openEditModal(emp)}
                          className="text-gray-400 hover:text-indigo-400 transition-colors p-2 hover:bg-indigo-50/10 rounded-lg inline-flex items-center gap-2 border border-gray-800"
                          title="Editar Cliente"
                        >
                          <Pencil size={16} />
                        </button>
                        <button 
                          onClick={() => handleDelete(emp.id)}
                          className="text-gray-400 hover:text-red-500 transition-colors p-2 hover:bg-red-50/10 rounded-lg inline-flex items-center gap-2 border border-gray-800"
                          title="Excluir Cliente"
                        >
                          <Trash size={16} />
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {/* Modal Novo Cliente */}
      {showModal && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex justify-center items-center p-4 z-50">
          <div className="bg-gray-900 border border-gray-800 rounded-2xl shadow-xl w-full max-w-lg overflow-hidden flex flex-col max-h-[90vh]">
            <div className="px-6 py-4 border-b border-gray-800 flex justify-between items-center bg-gray-950">
              <h2 className="text-xl font-bold text-white tracking-tight">Setup de Novo Cliente</h2>
              <button onClick={() => setShowModal(false)} className="text-gray-400 hover:text-white transition-colors">
                <X size={20} />
              </button>
            </div>
            
            <div className="p-6 overflow-y-auto custom-scrollbar">
              {error && (
                <div className="mb-6 p-3 bg-red-900/30 border border-red-800 text-red-300 rounded-lg text-sm">
                  {error}
                </div>
              )}
              
              <form id="setup-form" onSubmit={handleSubmit} className="space-y-6">
                
                {/* Dados da Empresa */}
                <div>
                  <h3 className="text-sm font-semibold text-indigo-400 uppercase tracking-wider mb-4 flex items-center gap-2">
                    <Building size={16} /> Dados da Empresa
                  </h3>
                  <div className="space-y-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-1">Nome da Empresa *</label>
                      <input 
                        required name="nome_empresa" value={formData.nome_empresa} onChange={handleChange}
                        className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 focus:border-indigo-600 outline-none transition-all"
                        placeholder="Ex: Clínica Sorriso"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-1">Área de Atuação</label>
                      <input 
                        name="area_atuacao" value={formData.area_atuacao} onChange={handleChange}
                        className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 focus:border-indigo-600 outline-none transition-all"
                        placeholder="Ex: Saúde / Odontologia"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Logo da Empresa (White-Label)</label>
                      <div className="flex items-center gap-4 mt-1">
                        {formData.logo_url && (
                          <div className="h-12 w-12 rounded-lg border border-gray-200 overflow-hidden flex-shrink-0 bg-gray-50 flex items-center justify-center">
                            <img src={formData.logo_url} alt="Logo Preview" className="max-h-full max-w-full object-contain" />
                          </div>
                        )}
                        <input
                          type="file"
                          accept=".png, .jpg, .jpeg, .svg"
                          onChange={handleLogoUpload}
                          className="block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-semibold file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100"
                        />
                      </div>
                      <p className="text-xs text-gray-500 mt-1">Selecione uma imagem para personalizar o painel deste cliente.</p>
                    </div>
                  </div>
                </div>

                <div className="border-t border-gray-800 my-2"></div>

                {/* Dados do Admin */}
                <div>
                  <h3 className="text-sm font-semibold text-indigo-400 uppercase tracking-wider mb-4 flex items-center gap-2">
                    <User size={16} /> Dados do Administrador
                  </h3>
                  <div className="space-y-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-1">Nome do Responsável *</label>
                      <input 
                        required name="admin_nome" value={formData.admin_nome} onChange={handleChange}
                        className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 focus:border-indigo-600 outline-none transition-all"
                        placeholder="João da Silva"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-1">Email de Login *</label>
                      <div className="relative">
                        <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                          <LogIn size={16} className="text-gray-500" />
                        </div>
                        <input 
                          type="email" required name="admin_email" value={formData.admin_email} onChange={handleChange}
                          className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 pl-10 pr-3 text-white focus:ring-2 focus:ring-indigo-600 focus:border-indigo-600 outline-none transition-all"
                          placeholder="joao@clinica.com"
                        />
                      </div>
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-1">Senha Inicial *</label>
                      <div className="relative">
                        <div className="absolute inset-y-0 left-0 pl-3 flex items-center pointer-events-none">
                          <KeyRound size={16} className="text-gray-500" />
                        </div>
                        <input 
                          type="text" required name="admin_senha" value={formData.admin_senha} onChange={handleChange}
                          className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 pl-10 pr-3 text-white focus:ring-2 focus:ring-indigo-600 focus:border-indigo-600 outline-none transition-all"
                          placeholder="Senha de acesso"
                        />
                      </div>
                    </div>
                  </div>
                </div>

              </form>
            </div>
            
            <div className="px-6 py-4 bg-gray-950 border-t border-gray-800 flex justify-end space-x-3">
              <button 
                type="button" onClick={() => setShowModal(false)}
                className="px-4 py-2 text-gray-300 font-medium hover:text-white transition-colors"
                disabled={submitting}
              >
                Cancelar
              </button>
              <button 
                type="submit" form="setup-form" disabled={submitting}
                className="bg-indigo-600 hover:bg-indigo-700 text-white px-6 py-2 rounded-lg font-medium transition-colors shadow-sm disabled:opacity-70 disabled:cursor-not-allowed"
              >
                {submitting ? "Criando ambiente..." : "Concluir Setup"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Modal Editar Cliente */}
      {showEditModal && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex justify-center items-center p-4 z-50">
          <div className="bg-gray-900 border border-gray-800 rounded-2xl shadow-xl w-full max-w-lg overflow-hidden flex flex-col">
            <div className="px-6 py-4 border-b border-gray-800 flex justify-between items-center bg-gray-950">
              <h2 className="text-xl font-bold text-white tracking-tight">Editar Cliente</h2>
              <button onClick={() => setShowEditModal(false)} className="text-gray-400 hover:text-white transition-colors">
                <X size={20} />
              </button>
            </div>
            
            <div className="p-6">
              <form id="edit-form" onSubmit={handleEditSubmit} className="space-y-6">
                <div>
                  <h3 className="text-sm font-semibold text-indigo-400 uppercase tracking-wider mb-4 flex items-center gap-2">
                    <Building size={16} /> Dados da Empresa
                  </h3>
                  <div className="space-y-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-1">Nome da Empresa *</label>
                      <input 
                        required name="nome_empresa" value={editFormData.nome_empresa} onChange={handleEditChange}
                        className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 focus:border-indigo-600 outline-none transition-all"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-1">Área de Atuação</label>
                      <input 
                        name="area_atuacao" value={editFormData.area_atuacao} onChange={handleEditChange}
                        className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 focus:border-indigo-600 outline-none transition-all"
                      />
                    </div>
                    <div>
                      <label className="block text-sm font-medium text-gray-700 mb-1">Logo da Empresa (White-Label)</label>
                      <div className="flex items-center gap-4 mt-1">
                        {editFormData.logo_url && (
                          <div className="h-12 w-12 rounded-lg border border-gray-200 overflow-hidden flex-shrink-0 bg-gray-50 flex items-center justify-center">
                            <img src={editFormData.logo_url} alt="Logo Preview" className="max-h-full max-w-full object-contain" />
                          </div>
                        )}
                        <input
                          type="file"
                          accept=".png, .jpg, .jpeg, .svg"
                          onChange={handleEditLogoUpload}
                          className="block w-full text-sm text-gray-500 file:mr-4 file:py-2 file:px-4 file:rounded-md file:border-0 file:text-sm file:font-semibold file:bg-blue-50 file:text-blue-700 hover:file:bg-blue-100"
                        />
                      </div>
                      <p className="text-xs text-gray-500 mt-1">Selecione uma imagem para personalizar o painel deste cliente.</p>
                    </div>
                  </div>
                </div>
              </form>
            </div>
            
            <div className="px-6 py-4 bg-gray-950 border-t border-gray-800 flex justify-end space-x-3">
              <button 
                type="button" onClick={() => setShowEditModal(false)}
                className="px-4 py-2 text-gray-300 font-medium hover:text-white transition-colors"
                disabled={submitting}
              >
                Cancelar
              </button>
              <button 
                type="submit" form="edit-form" disabled={submitting}
                className="bg-indigo-600 hover:bg-indigo-700 text-white px-6 py-2 rounded-lg font-medium transition-colors shadow-sm disabled:opacity-70 disabled:cursor-not-allowed"
              >
                {submitting ? "Salvando..." : "Salvar Alterações"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL INTEGRAÇÕES E CONEXÕES */}
      {showCredenciaisModal && empresaSelecionada && (
        <div className="fixed inset-0 bg-gray-900/50 flex items-center justify-center p-4 z-50">
          <div className="bg-white rounded-2xl shadow-xl w-full max-w-5xl overflow-hidden">
            <div className="px-6 py-4 border-b border-gray-100 flex justify-between items-center bg-gray-50/50">
              <div>
                <h2 className="text-xl font-bold text-gray-800 flex items-center gap-2">
                  <Settings className="text-gray-500" size={24} />
                  Integrações e Canais
                </h2>
                <p className="text-xs text-gray-500 mt-1">Empresa: {empresaSelecionada.nome_empresa}</p>
              </div>
              <button 
                onClick={() => setShowCredenciaisModal(false)}
                className="text-gray-400 hover:text-red-500 hover:bg-red-50 p-2 rounded-lg transition-colors"
              >
                <X size={20} />
              </button>
            </div>

            <div className="border-b border-gray-100 px-6 pt-4">
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setActiveIntegracaoTab("conexoes")}
                  className={`rounded-t-lg px-4 py-2 text-sm font-medium transition-colors ${
                    activeIntegracaoTab === "conexoes"
                      ? "bg-indigo-50 text-indigo-700 border border-b-white border-indigo-200"
                      : "text-gray-500 hover:text-gray-700 hover:bg-gray-50"
                  }`}
                >
                  Canais / Conexões
                </button>
                <button
                  type="button"
                  onClick={() => setActiveIntegracaoTab("legado")}
                  className={`rounded-t-lg px-4 py-2 text-sm font-medium transition-colors ${
                    activeIntegracaoTab === "legado"
                      ? "bg-indigo-50 text-indigo-700 border border-b-white border-indigo-200"
                      : "text-gray-500 hover:text-gray-700 hover:bg-gray-50"
                  }`}
                >
                  Credenciais Legadas
                </button>
              </div>
            </div>

            <div className="max-h-[80vh] overflow-y-auto p-6">
              {activeIntegracaoTab === "conexoes" ? (
                <EmpresaConexoesManager
                  empresaId={empresaSelecionada.id}
                  empresaNome={empresaSelecionada.nome_empresa}
                  conexaoDisparoId={empresaSelecionada.conexao_disparo_id}
                  disparoDelayMin={empresaSelecionada.disparo_delay_min}
                  disparoDelayMax={empresaSelecionada.disparo_delay_max}
                  onConexaoDisparoUpdated={handleConexaoDisparoUpdated}
                />
              ) : (
                <form onSubmit={handleSaveCredenciais} className="space-y-6">
                  <div className="rounded-xl border border-amber-200 bg-amber-50 px-4 py-3 text-sm text-amber-800">
                    Esta aba mantém a configuração antiga por compatibilidade. As novas integrações devem ser cadastradas em <strong>Canais / Conexões</strong>.
                  </div>

                  <div className="space-y-4">
                    <div>
                      <label className="block text-sm font-semibold text-gray-700 mb-1">
                        Evolution URL Base
                      </label>
                      <input
                        type="url"
                        required
                        placeholder="Ex: https://api.meuservidor.com"
                        className="w-full px-4 py-2 border border-gray-200 rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
                        value={credenciais.evolution_url}
                        onChange={(e) => setCredenciais({...credenciais, evolution_url: e.target.value})}
                      />
                    </div>
                    
                    <div>
                      <label className="block text-sm font-semibold text-gray-700 mb-1">
                        Global API Key
                      </label>
                      <input
                        type="password"
                        required
                        placeholder="Sua chave secreta da Evolution"
                        className="w-full px-4 py-2 border border-gray-200 rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
                        value={credenciais.evolution_apikey}
                        onChange={(e) => setCredenciais({...credenciais, evolution_apikey: e.target.value})}
                      />
                    </div>
                    
                    <div>
                      <label className="block text-sm font-semibold text-gray-700 mb-1">
                        Nome da Instância
                      </label>
                      <input
                        type="text"
                        required
                        placeholder="Ex: whatsapp-tenant-01"
                        className="w-full px-4 py-2 border border-gray-200 rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
                        value={credenciais.evolution_instance}
                        onChange={(e) => setCredenciais({...credenciais, evolution_instance: e.target.value})}
                      />
                    </div>

                    <div>
                      <label className="block text-sm font-semibold text-gray-700 mb-1">
                        Chave da OpenAI (Opcional - Chave do Cliente)
                      </label>
                      <input
                        type="password"
                        placeholder="sk-..."
                        className="w-full px-4 py-2 border border-gray-200 rounded-xl focus:ring-2 focus:ring-blue-500 focus:border-blue-500 outline-none transition-all"
                        value={credenciais.openai_api_key}
                        onChange={(e) => setCredenciais({...credenciais, openai_api_key: e.target.value})}
                      />
                    </div>
                  </div>

                  <div className="mt-8 flex justify-end">
                    <button
                      type="submit"
                      disabled={savingCredenciais}
                      className="bg-gray-900 hover:bg-black text-white px-6 py-2.5 rounded-xl font-medium transition-colors flex items-center gap-2 disabled:opacity-70 disabled:cursor-not-allowed"
                    >
                      {savingCredenciais ? (
                        <><RefreshCw size={18} className="animate-spin" /> Salvando...</>
                      ) : (
                        "Salvar Integração"
                      )}
                    </button>
                  </div>
                </form>
              )}
            </div>
          </div>
        </div>
      )}

      {/* MODAL CONFIGURAÇÃO IA */}
      {showConfigIAModal && empresaSelecionada && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4 z-50">
          <div className="bg-gray-900 border border-gray-800 rounded-2xl shadow-xl w-full max-w-lg overflow-hidden flex flex-col max-h-[90vh]">
            <div className="px-6 py-4 border-b border-gray-800 flex justify-between items-center bg-gray-950">
              <div>
                <h2 className="text-xl font-bold text-white flex items-center gap-2">
                  <Brain className="text-purple-500" size={24} />
                  Configuração do Agente
                </h2>
                <p className="text-xs text-gray-500 mt-1">Empresa: {empresaSelecionada.nome_empresa}</p>
              </div>
              <button 
                onClick={() => setShowConfigIAModal(false)}
                className="text-gray-400 hover:text-white transition-colors"
              >
                <X size={20} />
              </button>
            </div>

            <div className="p-6 overflow-y-auto custom-scrollbar">
              {loadingConfigIA ? (
                <div className="flex justify-center items-center py-10 text-gray-400">
                  <RefreshCw size={24} className="animate-spin" />
                </div>
              ) : (
                <form id="config-ia-form" onSubmit={handleSaveConfigIA} className="space-y-6">
                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-1">Nome do Agente Principal</label>
                    <input
                      type="text"
                      value={configIAData.nome_agente}
                      onChange={(e) => setConfigIAData({...configIAData, nome_agente: e.target.value})}
                      placeholder="Ex: Assistente Virtual"
                      className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-purple-600 focus:border-purple-600 outline-none transition-all"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-1">Mensagem de Saudação Inicial</label>
                    <textarea
                      value={configIAData.mensagem_saudacao}
                      onChange={(e) => setConfigIAData({...configIAData, mensagem_saudacao: e.target.value})}
                      rows={2}
                      placeholder="Ex: Olá! Sou o {nome_agente}, como posso te ajudar hoje?"
                      className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-purple-600 focus:border-purple-600 outline-none transition-all resize-y"
                    />
                  </div>
                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-1">Tom de Voz da IA</label>
                    <select
                      value={configIAData.ia_tom_voz}
                      onChange={(e) => setConfigIAData({...configIAData, ia_tom_voz: e.target.value})}
                      className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-purple-600 focus:border-purple-600 outline-none transition-all"
                    >
                      <option value="" disabled>Selecione um tom de voz</option>
                      <option value="Profissional">Profissional e Objetivo</option>
                      <option value="Amigável">Amigável e Empático</option>
                      <option value="Técnico">Técnico e Detalhista</option>
                      <option value="Vendedor Agressivo">Vendedor Agressivo/Persuasivo</option>
                    </select>
                  </div>

                  <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-1">Modelo do Atendente (IA)</label>
                      <select
                        value={configIAData.modelo_ia}
                        onChange={(e) => setConfigIAData({...configIAData, modelo_ia: e.target.value})}
                        className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-purple-600 focus:border-purple-600 outline-none transition-all"
                      >
                        {modelosDisponiveis.map(m => <option key={m} value={m}>{m}</option>)}
                        {!modelosDisponiveis.includes("gpt-4o-mini") && <option value="gpt-4o-mini">gpt-4o-mini</option>}
                      </select>
                    </div>

                    <div>
                      <label className="block text-sm font-medium text-gray-300 mb-1">Modelo do Roteador</label>
                      <select
                        value={configIAData.modelo_roteador}
                        onChange={(e) => setConfigIAData({...configIAData, modelo_roteador: e.target.value})}
                        className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-purple-600 focus:border-purple-600 outline-none transition-all"
                      >
                        {modelosDisponiveis.map(m => <option key={m} value={m}>{m}</option>)}
                        {!modelosDisponiveis.includes("gpt-4o-mini") && <option value="gpt-4o-mini">gpt-4o-mini</option>}
                      </select>
                    </div>
                  </div>

                  <div className="pt-4 border-t border-gray-800 space-y-4">
                    <h3 className="text-sm font-bold text-indigo-400 uppercase tracking-wider">
                      Roteador Semântico (Multi-Agentes)
                    </h3>
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
                      <div>
                        <label className="block text-xs font-medium text-gray-400 mb-1">Limite de Certeza</label>
                        <input
                          type="number"
                          min="0"
                          max="1"
                          step="0.01"
                          value={configIAData.limite_certeza}
                          onChange={(e) => setConfigIAData({...configIAData, limite_certeza: parseFloat(e.target.value) || 0})}
                          className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 outline-none"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-400 mb-1">Limite de Dúvida</label>
                        <input
                          type="number"
                          min="0"
                          max="1"
                          step="0.01"
                          value={configIAData.limite_duvida}
                          onChange={(e) => setConfigIAData({...configIAData, limite_duvida: parseFloat(e.target.value) || 0})}
                          className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 outline-none"
                        />
                      </div>
                      <div>
                        <label className="block text-xs font-medium text-gray-400 mb-1">Máx. Agentes de Desempate</label>
                        <input
                          type="number"
                          min="1"
                          step="1"
                          value={configIAData.max_agentes_desempate}
                          onChange={(e) => setConfigIAData({...configIAData, max_agentes_desempate: parseInt(e.target.value, 10) || 1})}
                          className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 outline-none"
                        />
                      </div>
                    </div>
                    <p className="text-xs text-gray-500">
                      Ajuste a sensibilidade para ativação automática e seleção por desempate do roteador multi-especialistas.
                    </p>
                  </div>

                  <div className="pt-4 border-t border-gray-800 space-y-4">
                    <h3 className="text-sm font-bold text-indigo-400 uppercase tracking-wider flex items-center gap-2">
                       Follow-up Automático
                    </h3>
                    
                    <label className="flex items-center gap-3 p-3 bg-gray-950 border border-gray-800 rounded-xl cursor-pointer hover:border-indigo-500 transition-colors">
                      <input
                        type="checkbox"
                        className="w-5 h-5 rounded text-indigo-600 focus:ring-indigo-500 bg-gray-900 border-gray-700"
                        checked={configIAData.followup_ativo}
                        onChange={(e) => setConfigIAData({...configIAData, followup_ativo: e.target.checked})}
                      />
                      <div>
                        <p className="text-sm font-semibold text-white">Ativar Reengajamento (Follow-up)</p>
                        <p className="text-xs text-gray-400 mt-0.5">Se ligado, a IA enviará mensagens automáticas se o lead parar de responder.</p>
                      </div>
                    </label>

                    {configIAData.followup_ativo && (
                      <div className="grid grid-cols-2 gap-4 animate-in fade-in slide-in-from-top-2 duration-300">
                        <div>
                          <label className="block text-xs font-medium text-gray-400 mb-1">Espera Nível 1 (Min)</label>
                          <input
                            type="number"
                            min="1"
                            value={configIAData.followup_espera_nivel_1_minutos}
                            onChange={(e) => setConfigIAData({...configIAData, followup_espera_nivel_1_minutos: parseInt(e.target.value) || 0})}
                            className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 outline-none"
                          />
                        </div>
                        <div>
                          <label className="block text-xs font-medium text-gray-400 mb-1">Espera Nível 2 (Min)</label>
                          <input
                            type="number"
                            min="1"
                            value={configIAData.followup_espera_nivel_2_minutos}
                            onChange={(e) => setConfigIAData({...configIAData, followup_espera_nivel_2_minutos: parseInt(e.target.value) || 0})}
                            className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-indigo-600 outline-none"
                          />
                        </div>
                      </div>
                    )}
                  </div>

                  <div>
                    <label className="block text-sm font-medium text-gray-300 mb-1">
                      Instruções Personalizadas
                    </label>
                    <textarea
                      value={configIAData.ia_instrucoes_personalizadas}
                      onChange={(e) => setConfigIAData({...configIAData, ia_instrucoes_personalizadas: e.target.value})}
                      rows={4}
                      placeholder="Diretrizes gerais, restrições ou objetivos principais do assistente."
                      className="w-full bg-gray-950 border border-gray-800 rounded-lg py-2 px-3 text-white focus:ring-2 focus:ring-purple-600 focus:border-purple-600 outline-none transition-all resize-y"
                    />
                  </div>
                </form>
              )}
            </div>
            
            <div className="px-6 py-4 bg-gray-950 border-t border-gray-800 flex justify-end space-x-3 mt-auto shrink-0">
              <button 
                type="button" onClick={() => setShowConfigIAModal(false)}
                className="px-4 py-2 text-gray-300 font-medium hover:text-white transition-colors"
                disabled={savingConfigIA}
              >
                Cancelar
              </button>
              <button 
                type="submit" form="config-ia-form" disabled={savingConfigIA || loadingConfigIA}
                className="bg-purple-600 hover:bg-purple-700 text-white px-6 py-2 rounded-lg font-medium transition-colors shadow-sm flex items-center gap-2 disabled:opacity-70 disabled:cursor-not-allowed"
              >
                {savingConfigIA ? <><RefreshCw size={16} className="animate-spin" /> Salvando...</> : "Salvar Configurações"}
              </button>
            </div>
          </div>
        </div>
      )}

      {/* MODAL TREINAMENTO RAG */}
      {showRagModal && empresaSelecionada && (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm flex justify-center items-center p-4 z-50">
          <div className="bg-gray-50 border border-gray-200 rounded-2xl shadow-xl w-full max-w-6xl overflow-hidden flex flex-col max-h-[95vh]">
            <div className="px-6 py-4 border-b border-gray-200 flex justify-between items-center bg-white">
              <h2 className="text-xl font-bold text-gray-800 flex items-center gap-2">
                <Brain className="text-blue-600" size={24} />
                Treinamento RAG: {empresaSelecionada.nome_empresa}
              </h2>
              <button onClick={() => setShowRagModal(false)} className="text-gray-400 hover:text-gray-600 transition-colors">
                <X size={20} />
              </button>
            </div>
            
            <div className="p-6 overflow-y-auto custom-scrollbar bg-gray-50">
              {/* Wrapped the RAG Manager in the modal */}
              <RAGManager empresaId={empresaSelecionada.id} />
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

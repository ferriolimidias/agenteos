import { useEffect, useState } from "react";
import { Outlet, Link, useNavigate } from "react-router-dom";
import { LayoutDashboard, Users, Brain, Calendar, LogOut, Bot, MessageSquare, Webhook, ArrowRightLeft, Megaphone, Tag } from "lucide-react";
import axios from "axios";
import { clearImpersonation, getStoredUser } from "../utils/auth";

export default function TenantLayout() {
  const navigate = useNavigate();
  const user = getStoredUser();
  const [configVisual, setConfigVisual] = useState({
    nomeSistema: "Minha Empresa",
    logoBase64: "",
  });

  const isImpersonating = localStorage.getItem("impersonating") === "true";
  const impersonatingEmpresa = localStorage.getItem("impersonating_empresa");

  const handleReturnToAdmin = () => {
    clearImpersonation();
    window.location.href = "/admin";
  };

  const handleLogout = () => {
    clearImpersonation();
    localStorage.removeItem("user");
    localStorage.removeItem("token");
    navigate("/");
  };

  useEffect(() => {
    const fetchConfig = async () => {
      try {
        const response = await axios.get("/api/admin/configuracoes");
        setConfigVisual({
          nomeSistema: response.data?.nome_sistema || "Minha Empresa",
          logoBase64: response.data?.logo_base64 || "",
        });
      } catch (error) {
        setConfigVisual({
          nomeSistema: "Minha Empresa",
          logoBase64: "",
        });
      }
    };

    fetchConfig();
    window.addEventListener("configuracoesUpdated", fetchConfig);
    return () => {
      window.removeEventListener("configuracoesUpdated", fetchConfig);
    };
  }, []);

  return (
    <div className="min-h-screen flex bg-gray-50 text-gray-900">
      {/* Sidebar */}
      <aside className="w-64 bg-blue-900 text-white p-6 flex flex-col shadow-xl">
        <div className="mb-8">
          {configVisual.logoBase64 ? (
            <img
              src={configVisual.logoBase64}
              alt="Logo"
              className="h-12 max-w-[180px] object-contain bg-white rounded-md p-1"
            />
          ) : (
            <h2 className="text-2xl font-bold truncate">{configVisual.nomeSistema}</h2>
          )}
          <span className="text-blue-300 text-sm font-medium">Painel de Controle</span>
        </div>
        
        <nav className="flex-1 space-y-2">
          <Link to="/painel" className="flex items-center space-x-3 text-blue-100 hover:text-white hover:bg-blue-800 px-3 py-2 rounded-lg transition-colors">
            <LayoutDashboard size={20} />
            <span>Dashboard</span>
          </Link>
          <Link to="/painel/crm" className="flex items-center space-x-3 text-blue-100 hover:text-white hover:bg-blue-800 px-3 py-2 rounded-lg transition-colors">
            <Users size={20} />
            <span>CRM Dinâmico</span>
          </Link>
          <Link to="/painel/rag" className="flex items-center space-x-3 text-blue-100 hover:text-white hover:bg-blue-800 px-3 py-2 rounded-lg transition-colors">
            <Brain size={20} />
            <span>Treinar IA (RAG)</span>
          </Link>
          <Link to="/painel/agenda" className="flex items-center space-x-3 text-blue-100 hover:text-white hover:bg-blue-800 px-3 py-2 rounded-lg transition-colors">
            <Calendar size={20} />
            <span>Agenda</span>
          </Link>
          <Link to="/painel/simulador" className="flex items-center space-x-3 text-blue-100 hover:text-white hover:bg-blue-800 px-3 py-2 rounded-lg transition-colors">
            <Bot size={20} />
            <span>Simulador</span>
          </Link>
          <Link to="/painel/inbox" className="flex items-center space-x-3 text-blue-100 hover:text-white hover:bg-blue-800 px-3 py-2 rounded-lg transition-colors">
            <MessageSquare size={20} />
            <span>Live Chat</span>
          </Link>
          <Link to="/painel/integracoes" className="flex items-center space-x-3 text-blue-100 hover:text-white hover:bg-blue-800 px-3 py-2 rounded-lg transition-colors">
            <Webhook size={20} />
            <span>Integrações</span>
          </Link>
          <Link to="/painel/transferencias" className="flex items-center space-x-3 text-blue-100 hover:text-white hover:bg-blue-800 px-3 py-2 rounded-lg transition-colors">
            <ArrowRightLeft size={20} />
            <span>Transferências</span>
          </Link>
          <Link to="/painel/campanhas" className="flex items-center space-x-3 text-blue-100 hover:text-white hover:bg-blue-800 px-3 py-2 rounded-lg transition-colors">
            <Megaphone size={20} />
            <span>Campanhas</span>
          </Link>
          <Link to="/painel/tags" className="flex items-center space-x-3 text-blue-100 hover:text-white hover:bg-blue-800 px-3 py-2 rounded-lg transition-colors">
            <Tag size={20} />
            <span>Gestão de Tags</span>
          </Link>
        </nav>
        
        <div className="pt-6 border-t border-blue-800">
          <button onClick={handleLogout} className="w-full flex items-center space-x-3 text-blue-300 hover:text-white hover:bg-blue-800 px-3 py-2 rounded-lg transition-colors">
            <LogOut size={20} />
            <span>Sair</span>
          </button>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 overflow-auto flex flex-col">
        {isImpersonating && (
          <div className="bg-emerald-600 text-white px-8 py-2 flex justify-between items-center text-sm font-medium shadow-sm z-10 relative">
            <span className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-emerald-300 animate-pulse"></span>
              Acessando como: <strong>{impersonatingEmpresa}</strong>
            </span>
            <button 
              onClick={handleReturnToAdmin}
              className="bg-emerald-800 hover:bg-emerald-900 px-4 py-1.5 rounded transition-colors flex items-center gap-2"
            >
              <LogOut size={14} /> Sair e Voltar
            </button>
          </div>
        )}
        <header className="bg-white shadow border-b border-gray-200 py-4 px-8 flex justify-between items-center z-0 relative">
          <h1 className="text-xl font-semibold text-gray-800">
            {user ? `Olá, ${user.nome}` : "Bem-vindo(a)"}
          </h1>
          <div className="flex items-center space-x-4">
            <div className="w-8 h-8 rounded-full bg-blue-500 overflow-hidden text-white flex items-center justify-center font-bold">
              {user && user.nome ? user.nome.charAt(0).toUpperCase() : "A"}
            </div>
          </div>
        </header>
        <div className="p-8 max-w-7xl mx-auto">
          <Outlet />
        </div>
      </main>
    </div>
  );
}

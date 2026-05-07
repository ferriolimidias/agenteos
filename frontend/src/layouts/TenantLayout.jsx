import { useEffect, useState } from "react";
import { Outlet, Link, useNavigate } from "react-router-dom";
import { LayoutDashboard, Brain, BookOpenText, Users, Webhook, LogOut, Timer } from "lucide-react";
import api from "../services/api";
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
    const isSuperAdmin = user?.role === "super_admin";
    const fetchConfig = async () => {
      if (!isSuperAdmin) {
        setConfigVisual({
          nomeSistema: "Minha Empresa",
          logoBase64: "",
        });
        return;
      }
      try {
        const response = await api.get("/admin/configuracoes");
        setConfigVisual({
          nomeSistema: response.data?.nome_sistema || "Minha Empresa",
          logoBase64: response.data?.logo_base64 || "",
        });
      } catch {
        setConfigVisual({
          nomeSistema: "Minha Empresa",
          logoBase64: "",
        });
      }
    };

    if (isSuperAdmin) {
      fetchConfig();
      window.addEventListener("configuracoesUpdated", fetchConfig);
    }
    return () => {
      window.removeEventListener("configuracoesUpdated", fetchConfig);
    };
  }, [user?.role]);

  return (
    <div className="min-h-screen flex bg-[#0b0b0c] text-gray-100">
      {/* Sidebar */}
      <aside className="w-64 bg-[#0f0f10] text-white p-5 flex flex-col border-r border-[#2d2d2d]">
        <div className="mb-8">
          {configVisual.logoBase64 ? (
            <img
              src={configVisual.logoBase64}
              alt="Logo"
              className="h-12 max-w-[180px] object-contain bg-white rounded-md p-1"
            />
          ) : (
            <h2 className="text-xl font-semibold truncate">{configVisual.nomeSistema}</h2>
          )}
          <span className="text-xs text-gray-400 font-medium tracking-wide">PAINEL CLIENTE</span>
        </div>
        
        <nav className="flex-1 space-y-2">
          <Link to="/painel" className="flex items-center space-x-3 text-gray-300 hover:text-white hover:bg-[#1a1a1b] px-3 py-2 rounded-lg transition-colors border border-transparent hover:border-[#2d2d2d]">
            <LayoutDashboard size={18} />
            <span>Dashboard</span>
          </Link>
          <Link to="/painel/agentes" className="flex items-center space-x-3 text-gray-300 hover:text-white hover:bg-[#1a1a1b] px-3 py-2 rounded-lg transition-colors border border-transparent hover:border-[#2d2d2d]">
            <Brain size={18} />
            <span>Meus Agentes</span>
          </Link>
          <Link to="/painel/followups" className="flex items-center space-x-3 text-gray-300 hover:text-white hover:bg-[#1a1a1b] px-3 py-2 rounded-lg transition-colors border border-transparent hover:border-[#2d2d2d]">
            <Timer size={18} />
            <span>Cadências / Follow-up</span>
          </Link>
          <Link to="/painel/rag" className="flex items-center space-x-3 text-gray-300 hover:text-white hover:bg-[#1a1a1b] px-3 py-2 rounded-lg transition-colors border border-transparent hover:border-[#2d2d2d]">
            <BookOpenText size={18} />
            <span>Base de Conhecimento</span>
          </Link>
          <Link to="/painel/crm" className="flex items-center space-x-3 text-gray-300 hover:text-white hover:bg-[#1a1a1b] px-3 py-2 rounded-lg transition-colors border border-transparent hover:border-[#2d2d2d]">
            <Users size={18} />
            <span>Leads</span>
          </Link>
          <Link to="/painel/integracoes" className="flex items-center space-x-3 text-gray-300 hover:text-white hover:bg-[#1a1a1b] px-3 py-2 rounded-lg transition-colors border border-transparent hover:border-[#2d2d2d]">
            <Webhook size={18} />
            <span>Conexão WhatsApp</span>
          </Link>
        </nav>
        
        <div className="pt-6 border-t border-[#2d2d2d]">
          <button onClick={handleLogout} className="w-full flex items-center space-x-3 text-red-400 hover:text-red-300 hover:bg-[#1a1a1b] px-3 py-2 rounded-lg transition-colors border border-transparent hover:border-[#2d2d2d]">
            <LogOut size={18} />
            <span>Sair</span>
          </button>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 overflow-auto flex flex-col bg-[#0b0b0c]">
        {isImpersonating && (
          <div className="bg-emerald-700/70 border-b border-emerald-500/40 text-white px-8 py-2 flex justify-between items-center text-sm font-medium shadow-sm z-10 relative">
            <span className="flex items-center gap-2">
              <span className="w-2 h-2 rounded-full bg-emerald-300 animate-pulse"></span>
              Acessando como: <strong>{impersonatingEmpresa}</strong>
            </span>
            <button 
              onClick={handleReturnToAdmin}
              className="bg-emerald-900/70 hover:bg-emerald-900 px-4 py-1.5 rounded transition-colors flex items-center gap-2"
            >
              <LogOut size={14} /> Sair e Voltar
            </button>
          </div>
        )}
        <header className="bg-[#101012] border-b border-[#2d2d2d] py-4 px-8 flex justify-between items-center z-0 relative">
          <h1 className="text-lg font-semibold text-gray-100">
            {user ? `Olá, ${user.nome}` : "Bem-vindo(a)"}
          </h1>
          <div className="flex items-center space-x-4">
            <div className="w-8 h-8 rounded-full bg-indigo-500/80 overflow-hidden text-white flex items-center justify-center font-bold">
              {user && user.nome ? user.nome.charAt(0).toUpperCase() : "A"}
            </div>
          </div>
        </header>
        <div className="p-8 max-w-7xl mx-auto w-full">
          <Outlet />
        </div>
      </main>
    </div>
  );
}

import { useEffect, useState } from "react";
import { Outlet, NavLink, useNavigate } from "react-router-dom";
import {
  LayoutDashboard,
  Brain,
  BookOpenText,
  Users,
  Webhook,
  LogOut,
  Timer,
  MessageCircle,
  Tags,
  Megaphone,
} from "lucide-react";
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
    const fetchConfig = async () => {
      try {
        const response = await api.get("/public/configuracoes-branding");
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

    fetchConfig();
    window.addEventListener("configuracoesUpdated", fetchConfig);
    return () => {
      window.removeEventListener("configuracoesUpdated", fetchConfig);
    };
  }, []);

  const navBaseClass =
    "mx-3 flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors duration-200";
  const navInactiveClass =
    "text-slate-400 hover:bg-slate-800 hover:text-white";
  const navActiveClass =
    "bg-indigo-600 text-white shadow-md";

  return (
    <div className="min-h-screen flex bg-slate-50 text-slate-900">
      {/* Sidebar */}
      <aside className="w-72 bg-zinc-950 text-white py-6 flex flex-col border-r border-zinc-800 shadow-2xl">
        <div className="mb-8 px-5">
          {configVisual.logoBase64 ? (
            <img
              src={configVisual.logoBase64}
              alt="Logo"
              className="h-12 max-w-[190px] object-contain rounded-lg bg-white p-1.5"
            />
          ) : (
            <h2 className="truncate text-xl font-semibold text-white">{configVisual.nomeSistema}</h2>
          )}
          <span className="mt-2 inline-block text-xs font-medium tracking-[0.18em] text-slate-400">
            PAINEL CLIENTE
          </span>
        </div>

        <nav className="flex-1 space-y-1.5">
          <NavLink to="/painel" end className={({ isActive }) => `${navBaseClass} ${isActive ? navActiveClass : navInactiveClass}`}>
            <LayoutDashboard className="h-5 w-5 shrink-0" />
            <span>Dashboard</span>
          </NavLink>
          <NavLink to="/painel/chat" className={({ isActive }) => `${navBaseClass} ${isActive ? navActiveClass : navInactiveClass}`}>
            <MessageCircle className="h-5 w-5 shrink-0" />
            <span>Chat (WhatsApp Web)</span>
          </NavLink>
          <NavLink to="/painel/crm" className={({ isActive }) => `${navBaseClass} ${isActive ? navActiveClass : navInactiveClass}`}>
            <Users className="h-5 w-5 shrink-0" />
            <span>Leads / CRM</span>
          </NavLink>
          <NavLink to="/painel/tags" className={({ isActive }) => `${navBaseClass} ${isActive ? navActiveClass : navInactiveClass}`}>
            <Tags className="h-5 w-5 shrink-0" />
            <span>Tags</span>
          </NavLink>
          <NavLink to="/painel/conexoes" className={({ isActive }) => `${navBaseClass} ${isActive ? navActiveClass : navInactiveClass}`}>
            <Webhook className="h-5 w-5 shrink-0" />
            <span>Conexões</span>
          </NavLink>
          <NavLink to="/painel/agentes" className={({ isActive }) => `${navBaseClass} ${isActive ? navActiveClass : navInactiveClass}`}>
            <Brain className="h-5 w-5 shrink-0" />
            <span>Agente</span>
          </NavLink>
          <NavLink to="/painel/followups" className={({ isActive }) => `${navBaseClass} ${isActive ? navActiveClass : navInactiveClass}`}>
            <Timer className="h-5 w-5 shrink-0" />
            <span>Cadências / Follow-up</span>
          </NavLink>
          <NavLink to="/painel/campanhas" className={({ isActive }) => `${navBaseClass} ${isActive ? navActiveClass : navInactiveClass}`}>
            <Megaphone className="h-5 w-5 shrink-0" />
            <span>Campanhas</span>
          </NavLink>
          <NavLink to="/painel/rag" className={({ isActive }) => `${navBaseClass} ${isActive ? navActiveClass : navInactiveClass}`}>
            <BookOpenText className="h-5 w-5 shrink-0" />
            <span>Base de Conhecimento</span>
          </NavLink>
        </nav>

        <div className="mt-6 border-t border-zinc-800 px-3 pt-6">
          <button onClick={handleLogout} className="flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium text-red-300 transition-colors duration-200 hover:bg-red-500/10 hover:text-red-200">
            <LogOut className="h-5 w-5 shrink-0" />
            <span>Sair</span>
          </button>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 overflow-auto flex flex-col bg-slate-50">
        {isImpersonating && (
          <div className="relative z-10 flex items-center justify-between border-b border-emerald-200 bg-emerald-50 px-8 py-2 text-sm font-medium text-emerald-800 shadow-sm">
            <span className="flex items-center gap-2">
              <span className="h-2 w-2 animate-pulse rounded-full bg-emerald-500"></span>
              Acessando como: <strong>{impersonatingEmpresa}</strong>
            </span>
            <button
              onClick={handleReturnToAdmin}
              className="flex items-center gap-2 rounded-lg border border-emerald-300 bg-white px-4 py-1.5 text-emerald-700 transition-colors hover:bg-emerald-100"
            >
              <LogOut size={14} /> Sair e Voltar
            </button>
          </div>
        )}
        <header className="relative z-0 flex items-center justify-between border-b border-slate-200 bg-white px-8 py-4 shadow-sm">
          <h1 className="text-lg font-medium text-slate-800">
            {user ? `Olá, ${user.nome}` : "Bem-vindo(a)"}
          </h1>
          <div className="flex items-center space-x-4">
            <div className="flex h-9 w-9 items-center justify-center overflow-hidden rounded-full bg-indigo-600 text-sm font-semibold text-white shadow-sm">
              {user && user.nome ? user.nome.charAt(0).toUpperCase() : "A"}
            </div>
          </div>
        </header>
        <div className="mx-auto w-full max-w-7xl p-8">
          <div className="bg-white rounded-xl border border-slate-200 shadow-sm p-6 md:p-8">
            <Outlet />
          </div>
        </div>
      </main>
    </div>
  );
}

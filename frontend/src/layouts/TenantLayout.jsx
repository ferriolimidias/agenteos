import { useEffect, useState } from "react";
import { Outlet, NavLink, useLocation, useNavigate } from "react-router-dom";
import {
  LayoutDashboard,
  Brain,
  BookOpenText,
  Users,
  LogOut,
  Timer,
  MessageCircle,
  Tags,
  Megaphone,
  CalendarDays,
  Headphones,
  BarChart3,
  Building2,
  Instagram as InstagramIcon,
} from "lucide-react";
import api from "../services/api";
import { clearImpersonation, getStoredUser } from "../utils/auth";
import { shellStyles } from "../utils/uiStyles";

export default function TenantLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const user = getStoredUser();
  const isReviewer = user?.role === "meta_app_reviewer";
  const isFunnelRoute = location.pathname === "/painel/funil";
  const isJevRoute = location.pathname === "/painel/inteligencia";
  const isInboxRoute = ["/painel/chat", "/painel/inbox", "/painel/conversas"].includes(location.pathname);

  useEffect(() => {
    if (!isReviewer) return;
    const allowed = new Set(["/painel/conexoes", "/painel/tags"]);
    if (!allowed.has(location.pathname)) navigate("/painel/conexoes", { replace: true });
  }, [isReviewer, location.pathname, navigate]);
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

  return (
    <div className={`flex ${isFunnelRoute ? "funnel-route-shell" : ""} ${isJevRoute ? "jev-route-shell" : ""} ${isInboxRoute ? "chat-shell h-dvh overflow-hidden" : shellStyles.page}`}>
      <aside className={`${shellStyles.sidebar} ${isInboxRoute ? "chat-shell-sidebar" : ""}`}>
        <div className={`${shellStyles.sidebarHeader} ${isInboxRoute ? "chat-shell-brand" : ""}`}>
          {configVisual.logoBase64 ? (
            <img
              src={configVisual.logoBase64}
              alt="Logo"
              className="h-12 max-w-[190px] rounded-lg border border-slate-200 bg-white object-contain p-1.5"
            />
          ) : (
            <h2 className="truncate text-xl font-semibold text-slate-950">{configVisual.nomeSistema}</h2>
          )}
          <span className="mt-2 inline-block rounded-full border border-emerald-100 bg-emerald-50 px-2.5 py-1 text-[11px] font-semibold tracking-[0.16em] text-emerald-700">
            PAINEL CLIENTE
          </span>
        </div>

        <nav className="flex-1 space-y-1.5 overflow-y-auto py-5">
          {isReviewer ? (
            <>
              <p className={shellStyles.navSection}>Demonstração Meta</p>
              <NavLink to="/painel/conexoes" className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
                <MessageCircle className="h-5 w-5 shrink-0" />
                <span>Conexões WhatsApp</span>
              </NavLink>
              <NavLink to="/painel/tags" className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
                <Tags className="h-5 w-5 shrink-0" />
                <span>Gestão de Tags</span>
              </NavLink>
            </>
          ) : (
          <>
          <p className={shellStyles.navSection}>Operação</p>
          <NavLink to="/painel" end className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
            <LayoutDashboard className="h-5 w-5 shrink-0" />
            <span>Dashboard</span>
          </NavLink>
          <NavLink to="/painel/funil" className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}><BarChart3 className="h-5 w-5 shrink-0" /><span>Funil e gargalos</span></NavLink>
          <NavLink to="/painel/inteligencia" className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}><Brain className="h-5 w-5 shrink-0" /><span>Inteligência SHADOW</span></NavLink>
          <NavLink to="/painel/chat" className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
            <MessageCircle className="h-5 w-5 shrink-0" />
            <span>Inbox WhatsApp</span>
          </NavLink>
          <NavLink to="/painel/conexoes" className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
            <MessageCircle className="h-5 w-5 shrink-0" />
            <span>Conexão WhatsApp</span>
          </NavLink>
          <NavLink to="/painel/instagram" className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
            <InstagramIcon className="h-5 w-5 shrink-0" />
            <span>Instagram</span>
          </NavLink>
          <NavLink to="/painel/trafego/meta" end className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
            <BarChart3 className="h-5 w-5 shrink-0" />
            <span>Tráfego Meta</span>
          </NavLink>
          <NavLink to="/painel/trafego/meta/conversoes" className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
            <BarChart3 className="h-5 w-5 shrink-0" />
            <span>Conversões Meta</span>
          </NavLink>
          <NavLink to="/painel/crm" className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
            <Users className="h-5 w-5 shrink-0" />
            <span>Leads / CRM</span>
          </NavLink>
          <NavLink to="/painel/tags" className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
            <Tags className="h-5 w-5 shrink-0" />
            <span>Tags</span>
          </NavLink>
          <NavLink to="/painel/agenda" className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
            <CalendarDays className="h-5 w-5 shrink-0" />
            <span>Agenda</span>
          </NavLink>

          <p className={shellStyles.navSection}>Automações</p>
          <NavLink to="/painel/followups" className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
            <Timer className="h-5 w-5 shrink-0" />
            <span>Follow-ups</span>
          </NavLink>
          <NavLink to="/painel/campanhas" className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
            <Megaphone className="h-5 w-5 shrink-0" />
            <span>Campanhas</span>
          </NavLink>

          <p className={shellStyles.navSection}>Conhecimento e equipe</p>
          <NavLink to="/painel/rag" className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
            <BookOpenText className="h-5 w-5 shrink-0" />
            <span>Base de Conhecimento</span>
          </NavLink>
          <NavLink to="/painel/transferencias" className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
            <Headphones className="h-5 w-5 shrink-0" />
            <span>Atendimento Humano</span>
          </NavLink>
          <NavLink to="/painel/agentes" className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
            <Brain className="h-5 w-5 shrink-0" />
            <span>Agente de Atendimento</span>
          </NavLink>

          <p className={shellStyles.navSection}>Conta</p>
          <NavLink to="/painel/unidades" className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
            <Building2 className="h-5 w-5 shrink-0" />
            <span>Configurações da Conta</span>
          </NavLink>
          <NavLink to="/painel/simulador" className={({ isActive }) => `${shellStyles.navItem} ${isActive ? shellStyles.navActive : shellStyles.navInactive}`}>
            <BarChart3 className="h-5 w-5 shrink-0" />
            <span>Simulação simples</span>
          </NavLink>
          </>
          )}
        </nav>

        <div className="border-t border-slate-100 p-3">
          <button onClick={handleLogout} className="flex w-full items-center gap-3 rounded-lg border border-transparent px-3 py-2.5 text-sm font-medium text-red-600 transition-colors duration-200 hover:border-red-100 hover:bg-red-50 hover:text-red-700">
            <LogOut className="h-5 w-5 shrink-0" />
            <span>Sair</span>
          </button>
        </div>
      </aside>

      <main className={`flex min-w-0 flex-1 flex-col ${isInboxRoute ? "min-h-0 overflow-hidden" : "overflow-auto"}`}>
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
        <header className={`${shellStyles.topbar} ${isInboxRoute ? "hidden" : ""}`}>
          <h1 className="text-sm font-medium text-slate-600">
            {user ? `Olá, ${user.nome}` : "Bem-vindo(a)"}
          </h1>
          <div className="flex items-center space-x-4">
            <div className="flex h-9 w-9 items-center justify-center overflow-hidden rounded-full bg-indigo-600 text-sm font-semibold text-white shadow-sm">
              {user && user.nome ? user.nome.charAt(0).toUpperCase() : "A"}
            </div>
          </div>
        </header>
        <div className={`${isInboxRoute ? "chat-shell-content" : shellStyles.content} ${isInboxRoute ? "flex min-h-0 flex-1 flex-col overflow-hidden" : ""}`}>
          <Outlet />
        </div>
      </main>
    </div>
  );
}

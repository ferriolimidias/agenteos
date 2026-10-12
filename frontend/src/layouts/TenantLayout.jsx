import { useEffect, useRef, useState } from "react";
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
  Menu,
  X,
} from "lucide-react";
import api from "../services/api";
import { clearImpersonation, getStoredUser } from "../utils/auth";
import { shellStyles } from "../utils/uiStyles";

export default function TenantLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const user = getStoredUser();
  const isReviewer = user?.role === "meta_app_reviewer";
  const [menuOpen, setMenuOpen] = useState(false);
  const sidebarRef = useRef(null);
  const menuToggleRef = useRef(null);

  useEffect(() => {
    const desktop = window.matchMedia("(min-width: 1024px)");
    const closeOnDesktop = () => { if (desktop.matches) setMenuOpen(false); };
    desktop.addEventListener("change", closeOnDesktop);
    return () => desktop.removeEventListener("change", closeOnDesktop);
  }, []);

  useEffect(() => {
    if (!menuOpen) return;
    const sidebar = sidebarRef.current;
    const menuToggle = menuToggleRef.current;
    const focusables = () => [...sidebar.querySelectorAll("a[href], button:not([disabled])")];
    const initialFocus = requestAnimationFrame(() => focusables()[0]?.focus({ preventScroll: true }));
    const handleKeyDown = (event) => {
      if (event.key === "Escape") { event.preventDefault(); setMenuOpen(false); }
      if (event.key !== "Tab") return;
      const items = focusables();
      const first = items[0];
      const last = items.at(-1);
      if (!sidebar.contains(document.activeElement)) {
        event.preventDefault();
        (event.shiftKey ? last : first)?.focus({ preventScroll: true });
        return;
      }
      if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
      if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
    };
    document.addEventListener("keydown", handleKeyDown);
    return () => {
      cancelAnimationFrame(initialFocus);
      document.removeEventListener("keydown", handleKeyDown);
      requestAnimationFrame(() => menuToggle?.focus());
    };
  }, [menuOpen]);

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
    <div className={`${shellStyles.page} ${isInboxRoute ? "fm-shell-inbox chat-shell" : ""}`}>
      <a href="#tenant-content" className="fm-skip-link">Ir para o conteúdo</a>
      <aside ref={sidebarRef} id="tenant-navigation" className={`${shellStyles.sidebar} ${menuOpen ? "fm-sidebar-open" : ""}`} role={menuOpen ? "dialog" : undefined} aria-modal={menuOpen ? "true" : undefined} aria-label="Navegação do cliente">
        <button type="button" className="fm-menu-close" aria-label="Fechar menu" onClick={() => setMenuOpen(false)}><X size={20} aria-hidden="true" /></button>
        <div className={shellStyles.sidebarHeader}>
          {configVisual.logoBase64 ? (
            <img
              src={configVisual.logoBase64}
              alt={`Logo ${configVisual.nomeSistema}`}
              className="fm-brand-logo"
            />
          ) : (
            <h2 className="fm-brand-name">{configVisual.nomeSistema}</h2>
          )}
          <span className="fm-brand-role">
            PAINEL CLIENTE
          </span>
        </div>

        <nav className="fm-sidebar-nav" aria-label="Menu principal" onClick={(event) => { if (event.target.closest("a")) setMenuOpen(false); }}>
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

        <div className="fm-sidebar-footer">
          <button onClick={handleLogout} className={shellStyles.navItem}>
            <LogOut className="h-5 w-5 shrink-0" />
            <span>Sair</span>
          </button>
        </div>
      </aside>

      <button type="button" className="fm-shell-backdrop" tabIndex={-1} aria-label="Fechar menu" onClick={() => setMenuOpen(false)} />
      <main className="fm-shell-main" {...(menuOpen ? { inert: "" } : {})}>
        {isImpersonating && (
          <div className="fm-impersonation">
            <span className="flex items-center gap-2">
              Acessando como: <strong>{impersonatingEmpresa}</strong>
            </span>
            <button
              onClick={handleReturnToAdmin}
              className="fm-return-admin"
            >
              <LogOut size={14} /> Sair e Voltar
            </button>
          </div>
        )}
        <header className={shellStyles.topbar}>
          <button ref={menuToggleRef} type="button" className="fm-menu-toggle" aria-label="Abrir menu" aria-expanded={menuOpen} aria-controls="tenant-navigation" onClick={() => setMenuOpen(true)}><Menu size={20} aria-hidden="true" /></button>
          <div className="fm-topbar-context">
            <span className="fm-topbar-label">Painel do cliente</span>
            <span className="fm-topbar-title">{user ? `Olá, ${user.nome}` : "Bem-vindo(a)"}</span>
          </div>
          <span className="fm-avatar" aria-hidden="true">{user?.nome ? user.nome.charAt(0).toUpperCase() : "A"}</span>
        </header>
        <div id="tenant-content" tabIndex={-1} className={isInboxRoute ? "chat-shell-content" : shellStyles.content}>
          <Outlet />
        </div>
      </main>
    </div>
  );
}

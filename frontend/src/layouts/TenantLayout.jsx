import { useEffect, useRef, useState } from "react";
import { Outlet, Link, useLocation, useNavigate } from "react-router-dom";
import { LogOut, Menu, X } from "lucide-react";
import api from "../services/api";
import { clearImpersonation, getStoredUser } from "../utils/auth";
import { shellStyles } from "../utils/uiStyles";
import TenantNavigation from "../components/TenantNavigation";
import { isTenantInboxPath, navigationPreferenceKey, resolveTenantNavigation } from "../utils/tenantNavigation";
import "../styles/tenant-navigation.css";

export default function TenantLayout() {
  const navigate = useNavigate();
  const location = useLocation();
  const user = getStoredUser();
  const isReviewer = user?.role === "meta_app_reviewer";
  const reviewerRouteAllowed = ["/painel/conexoes", "/painel/tags"].includes(location.pathname);
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
    const focusables = () => [...sidebar.querySelectorAll("a[href], button:not([disabled])")].filter((item) => item.getClientRects().length > 0 && getComputedStyle(item).visibility !== "hidden");
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

  const isInboxRoute = isTenantInboxPath(location.pathname);
  const navigationContext = resolveTenantNavigation(location.pathname, user?.role);
  let navigationEmpresaId = user?.empresa_id;
  try { navigationEmpresaId = localStorage.getItem("impersonated_empresa_id") || navigationEmpresaId; } catch { /* Visual preferences remain optional. */ }
  const preferenceKey = navigationPreferenceKey(navigationEmpresaId, user?.role);

  useEffect(() => {
    if (!isReviewer) return;
    if (!reviewerRouteAllowed) navigate("/painel/conexoes", { replace: true });
  }, [isReviewer, reviewerRouteAllowed, navigate]);
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

        <TenantNavigation
          key={preferenceKey}
          role={user?.role}
          preferenceKey={preferenceKey}
          context={navigationContext}
          visitKey={location.key}
          onNavigate={() => setMenuOpen(false)}
        />

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
        <header className={`${shellStyles.topbar} fm-tenant-topbar`}>
          <button ref={menuToggleRef} type="button" className="fm-menu-toggle" aria-label="Abrir menu" aria-expanded={menuOpen} aria-controls="tenant-navigation" onClick={() => setMenuOpen(true)}><Menu size={20} aria-hidden="true" /></button>
          <div className="fm-topbar-context">
            <nav className="fm-tenant-breadcrumb" aria-label="Breadcrumb">
              <ol>
                {isReviewer ? (
                  <li><span>Demonstração Meta</span></li>
                ) : (
                  <li>{navigationContext?.item.id === "inbox" ? <span aria-current="page">Conversas</span> : <Link to="/painel/inbox">Conversas</Link>}</li>
                )}
                {navigationContext && navigationContext.item.id !== "inbox" && (
                  <>
                    <li><span>{navigationContext.group.label}</span></li>
                    <li>{navigationContext.detail ? <Link to={navigationContext.item.to}>{navigationContext.item.label}</Link> : <span aria-current="page">{navigationContext.item.label}</span>}</li>
                    {navigationContext.detail && <li><span aria-current="page">{navigationContext.detail}</span></li>}
                  </>
                )}
              </ol>
            </nav>
            <span className="fm-topbar-title">{navigationContext?.detail || navigationContext?.item.label || "Painel do cliente"}</span>
          </div>
          <span className="fm-avatar" aria-hidden="true">{user?.nome ? user.nome.charAt(0).toUpperCase() : "A"}</span>
        </header>
        <div id="tenant-content" tabIndex={-1} className={isInboxRoute ? "chat-shell-content" : shellStyles.content}>
          {(!isReviewer || reviewerRouteAllowed) && <Outlet />}
        </div>
      </main>
    </div>
  );
}

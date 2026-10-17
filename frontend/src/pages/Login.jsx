import { useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { AlertCircle, LogIn, MessageCircle, ShieldCheck, Sparkles } from "lucide-react";
import api from "../services/api";
import { clearImpersonation } from "../utils/auth";
import { ActionButton, InfoBanner } from "../components/ui";

export default function Login() {
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [senha, setSenha] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [configVisual, setConfigVisual] = useState({
    nomeSistema: "Antigravity OS",
    logoBase64: "",
  });

  useEffect(() => {
    const fetchConfig = async () => {
      try {
        const response = await api.get("/public/configuracoes-branding");
        setConfigVisual({
          nomeSistema: response.data?.nome_sistema || "Antigravity OS",
          logoBase64: response.data?.logo_base64 || "",
        });
      } catch {
        setConfigVisual({
          nomeSistema: "Antigravity OS",
          logoBase64: "",
        });
      }
    };

    fetchConfig();
  }, []);

  const handleLogin = async (e) => {
    e.preventDefault();
    setError("");
    setLoading(true);

    try {
      const response = await api.post("/auth/login", { email, senha });
      const { usuario, access_token } = response.data;
      
      // Salva os dados do usuário
      clearImpersonation();
      localStorage.setItem("user", JSON.stringify(usuario));
      localStorage.setItem("token", access_token);
      
      // Roteamento baseado na role
      if (usuario.role === "super_admin") {
        navigate("/admin");
      } else if (usuario.role === "meta_app_reviewer") {
        navigate("/painel/conexoes");
      } else {
        navigate("/painel/inbox");
      }
    } catch (err) {
      if (err.response && err.response.data && err.response.data.detail) {
        setError(err.response.data.detail);
      } else {
        setError("Erro de rede ao conectar com o servidor.");
      }
    } finally {
      setLoading(false);
    }
  };

  return (
    <div className="grid min-h-screen bg-slate-950 text-slate-950 lg:grid-cols-[1.05fr_0.95fr]">
      <section className="hidden min-h-screen flex-col justify-between border-r border-slate-800 bg-slate-950 p-10 text-white lg:flex">
        <div>
          <div className="inline-flex items-center gap-2 rounded-full border border-indigo-400/30 bg-indigo-400/10 px-3 py-1 text-xs font-semibold text-indigo-200">
            <Sparkles size={13} />
            Plataforma SaaS multi-tenant
          </div>
          <h1 className="mt-8 max-w-xl text-4xl font-semibold tracking-tight">
            Atendimento automatizado para WhatsApp com operação, CRM e agentes em um só painel.
          </h1>
          <p className="mt-5 max-w-lg text-sm leading-6 text-slate-400">
            Controle empresas, conversas, bases de conhecimento, campanhas e a arquitetura produtiva de agentes do AgenteOS.
          </p>
        </div>

        <div className="grid gap-3">
          {[
            { icon: MessageCircle, title: "Inbox WhatsApp", text: "Operação diária com histórico e transferência humana." },
            { icon: ShieldCheck, title: "Super Admin", text: "Gestão centralizada de tenants, arquitetura e configurações." },
            { icon: Sparkles, title: "Atendimento inteligente", text: "AtendimentoAgent como voz final com ferramentas tenant-scoped." },
          ].map((item) => {
            const Icon = item.icon;
            return (
              <div key={item.title} className="rounded-xl border border-slate-800 bg-slate-900/70 p-4">
                <div className="flex items-start gap-3">
                  <div className="rounded-lg bg-indigo-500/15 p-2 text-indigo-200">
                    <Icon size={18} />
                  </div>
                  <div>
                    <p className="text-sm font-semibold text-white">{item.title}</p>
                    <p className="mt-1 text-sm text-slate-400">{item.text}</p>
                  </div>
                </div>
              </div>
            );
          })}
        </div>
      </section>

      <main className="flex min-h-screen items-center justify-center bg-slate-100 p-4">
        <div className="w-full max-w-md rounded-2xl border border-slate-200 bg-white p-8 shadow-xl">
          <div className="mb-8 text-center">
          {configVisual.logoBase64 ? (
            <img
              src={configVisual.logoBase64}
              alt="Logo"
              className="mx-auto h-14 max-w-[220px] rounded-lg border border-slate-200 bg-white object-contain p-1"
            />
          ) : (
            <h1 className="text-3xl font-semibold tracking-tight text-slate-950">{configVisual.nomeSistema}</h1>
          )}
          <p className="mt-2 text-sm text-slate-500">Entre para acessar sua operação.</p>
        </div>
        
        {error && (
          <div className="mb-4">
            <InfoBanner tone="danger" icon={AlertCircle}>{error}</InfoBanner>
          </div>
        )}

        <form onSubmit={handleLogin} className="flex flex-col space-y-5">
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700" htmlFor="email">Email</label>
            <input 
              id="email"
              type="email" 
              required
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2.5 outline-none transition-all focus:border-indigo-600 focus:ring-2 focus:ring-indigo-100"
              placeholder="seu@email.com"
            />
          </div>
          
          <div>
            <label className="mb-1 block text-sm font-medium text-slate-700" htmlFor="senha">Senha</label>
            <input 
              id="senha"
              type="password" 
              required
              value={senha}
              onChange={(e) => setSenha(e.target.value)}
              className="w-full rounded-lg border border-slate-300 px-3 py-2.5 outline-none transition-all focus:border-indigo-600 focus:ring-2 focus:ring-indigo-100"
              placeholder="••••••••"
            />
          </div>
          
          <ActionButton
            type="submit"
            disabled={loading}
            icon={loading ? undefined : LogIn}
            className="mt-2 w-full py-3"
          >
            {loading ? "Entrando..." : "Entrar no sistema"}
          </ActionButton>
        </form>
          <p className="mt-6 text-center text-xs text-slate-500">
            Acesso protegido para administradores e equipes do tenant.
          </p>
        </div>
      </main>
    </div>
  );
}

import { Link, NavLink, Outlet, useParams } from "react-router-dom";
import { ArrowLeft, BrainCircuit } from "lucide-react";
import { PageHeader } from "../../components/ui";

export default function AdminAtendimentoIA() {
  const { empresaId } = useParams();
  const base = `/admin/empresas/${empresaId}/atendimento-ia`;
  const tabClass = ({ isActive }) => `inline-flex min-h-11 items-center rounded-lg px-4 text-sm font-semibold transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-700 ${isActive ? "bg-indigo-700 text-white" : "border border-slate-200 bg-white text-slate-700 hover:bg-slate-50"}`;

  return (
    <main className="space-y-5">
      <Link to={`/admin/empresas/${empresaId}?tab=agente`} className="inline-flex min-h-11 items-center gap-2 rounded-lg px-2 text-sm font-semibold text-slate-700 hover:text-indigo-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-700">
        <ArrowLeft size={16} aria-hidden="true" />
        Voltar à configuração da empresa
      </Link>
      <PageHeader title="Atendimento e IA" description="Configurações e ferramentas operacionais da empresa, sob administração da Ferrioli Mídias." icon={BrainCircuit} eyebrow="Super Admin · Empresa" />
      <nav aria-label="Seções de atendimento e IA" className="flex flex-wrap gap-2">
        <NavLink to={`${base}/prompts`} className={tabClass}>Prompts e versões</NavLink>
        <NavLink to={`${base}/jev`} className={tabClass}>Diagnósticos</NavLink>
      </nav>
      <section aria-label="Conteúdo de atendimento e IA" className="min-w-0">
        <Outlet />
      </section>
    </main>
  );
}

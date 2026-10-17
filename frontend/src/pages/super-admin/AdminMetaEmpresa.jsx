import { Link, useParams } from "react-router-dom";
import { ArrowLeft } from "lucide-react";
import TrafficMeta from "../tenant/TrafficMeta";

export default function AdminMetaEmpresa() {
  const { empresaId } = useParams();
  return (
    <main className="space-y-5">
      <Link to={`/admin/empresas/${empresaId}?tab=canais`} className="inline-flex min-h-11 items-center gap-2 rounded-lg px-2 text-sm font-semibold text-slate-700 hover:text-indigo-700 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-indigo-700">
        <ArrowLeft size={16} aria-hidden="true" />
        Voltar aos canais da empresa
      </Link>
      <TrafficMeta adminTools />
    </main>
  );
}

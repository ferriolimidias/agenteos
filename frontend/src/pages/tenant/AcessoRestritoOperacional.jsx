import "../../styles/ux05-settings.css";

import { LockKeyhole, MessageCircle } from "lucide-react";
import { Link } from "react-router-dom";
import { PageHeader, RestrictedNotice } from "../../components/ui";

export default function AcessoRestritoOperacional({
  title = "Área administrada pela equipe Ferrioli",
  description = "Esta área é operada pela equipe Ferrioli. As Conversas e tarefas comerciais continuam disponíveis no painel.",
}) {
  return (
    <div className="fm-settings fm-restricted space-y-6">
      <PageHeader
        title={title}
        description={description}
        icon={LockKeyhole}
        eyebrow="Painel cliente"
      />

      <RestrictedNotice
        title="Acesso restrito para este perfil"
        description="Para consultar conversas, responder clientes ou transferir um atendimento, abra a Caixa de entrada."
      >
          <Link
            to="/painel/inbox"
            className="inline-flex min-h-11 items-center justify-center gap-2 rounded-lg border border-amber-200 bg-white px-3.5 py-2 text-sm font-semibold text-amber-900 shadow-sm transition-colors hover:bg-amber-100 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-amber-700"
          >
            <MessageCircle size={16} aria-hidden="true" />
            Abrir conversas
          </Link>
      </RestrictedNotice>
    </div>
  );
}

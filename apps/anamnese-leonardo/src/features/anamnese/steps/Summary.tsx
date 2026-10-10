import { useEffect, useRef, useState } from "react";
import {
  AlertCircle,
  CalendarDays,
  CheckCircle2,
  Dumbbell,
  MapPin,
  ShieldCheck,
  Target,
  TrendingUp,
  User as UserIcon,
} from "lucide-react";
import type { AnamneseData } from "../lib/types";
import type { AnamneseSession } from "../lib/api";
import { trackEvent } from "../lib/tracking";

const GOAL: Record<string, string> = {
  emagrecer: "Emagrecer",
  massa: "Ganhar massa",
  condicionamento: "Melhorar condicionamento",
  constancia: "Voltar com constância",
  correr: "Correr melhor",
  forca: "Ganhar força",
  saude: "Saúde e disposição",
  outro: "Outro",
};
const TYPE: Record<string, string> = {
  musculacao: "Musculação",
  corrida: "Corrida",
  hibrido: "Musculação + corrida",
  casa: "Treino em casa",
  naosei: "Ainda não sei",
};
const LEVEL: Record<string, string> = {
  iniciante: "Iniciante",
  parado: "Retomando",
  intermediario: "Intermediário",
  avancado: "Avançado",
};
const PLACE: Record<string, string> = {
  academia: "Academia",
  casa: "Casa",
  rua: "Rua/parque",
  academia_corrida: "Academia + corrida",
  casa_corrida: "Casa + corrida",
  naosei: "Ainda não sei",
};
const GENDER: Record<string, string> = {
  homem: "Homem",
  mulher: "Mulher",
  nao_informar: "Prefere não informar",
};
const LIMIT: Record<string, string> = {
  nao: "Nenhuma",
  joelho: "Joelho",
  coluna: "Coluna",
  ombro: "Ombro",
  quadril: "Quadril",
  tornozelo: "Tornozelo",
  punho: "Punho/cotovelo",
  outra: "Outra",
};
const DIFF: Record<string, string> = {
  constancia: "Falta de constância",
  direcao: "Falta de direção",
  tempo: "Falta de tempo",
  oque: "Não saber o que treinar",
  parar: "Começar e parar",
  motivacao: "Falta de motivação",
  medo: "Medo de fazer errado",
};

const label = (map: Record<string, string>, v: string) => map[v] ?? v ?? "-";

type Props = {
  data: AnamneseData;
  session: AnamneseSession | null;
  onContinue: () => Promise<{ checkout?: { url: string; provider?: string } | null } | void>;
};

export function Summary({ data, session, onContinue }: Props) {
  const t = data.trainingProfile;
  const submittedRef = useRef(false);
  const [status, setStatus] = useState<"sending" | "success" | "checkout_error" | "error">("sending");
  const [checkoutUrl, setCheckoutUrl] = useState<string | null>(session?.checkout?.url || null);

  useEffect(() => {
    if (submittedRef.current) return;
    submittedRef.current = true;
    onContinue()
      .then((result) => {
        if (result?.checkout?.url) {
          setCheckoutUrl(result.checkout.url);
          setStatus("success");
          trackEvent("PaymentScreenViewed", { leadToken: data.leadToken });
        } else {
          setStatus("checkout_error");
        }
      })
      .catch(() => setStatus("error"));
  }, [data.leadToken, onContinue]);

  const summaryCards = [
    { icon: UserIcon, k: "Nome", v: data.personalData.name || "-" },
    { icon: Target, k: "Objetivo principal", v: label(GOAL, t.mainGoal) },
    ...(data.personalData.gender
      ? [{ icon: UserIcon, k: "Perfil", v: label(GENDER, data.personalData.gender) }]
      : []),
    { icon: Dumbbell, k: "Foco do treino", v: label(TYPE, t.trainingType) },
    { icon: TrendingUp, k: "Nível atual", v: label(LEVEL, t.currentLevel) },
    {
      icon: CalendarDays,
      k: "Frequência semanal",
      v: t.daysPerWeek ? `${t.daysPerWeek} dias` : "-",
    },
    { icon: MapPin, k: "Local de treino", v: label(PLACE, t.trainingPlace) },
    {
      icon: AlertCircle,
      k: "Limitações",
      v:
        t.limitations.length === 0
          ? "-"
          : t.limitations.map((x) => LIMIT[x] ?? x).join(", "),
    },
    { icon: AlertCircle, k: "Maior dificuldade", v: label(DIFF, t.mainDifficulty) },
  ];

  return (
    <div className="min-h-[100dvh]">
      <section className="px-5 pt-8 pb-6 max-w-xl w-full mx-auto">
        <div className="animate-step-in">
          <div className="inline-flex items-center gap-2 rounded-full bg-primary/10 border border-primary/30 px-3 py-1 mb-4">
            <CheckCircle2 className="w-4 h-4 text-primary" />
            <span className="text-xs font-semibold text-primary">
              {session?.oferta_nome || "Avaliação"}
            </span>
          </div>
          <h1 className="font-heading text-3xl sm:text-4xl leading-tight mb-3">
            {status === "sending" ? "Enviando avaliação" : "Avaliação concluída"}
          </h1>
          {status === "sending" && (
            <p className="text-muted-foreground">
              Estamos salvando tuas respostas com segurança.
            </p>
          )}
          {status === "success" && (
            <div className="space-y-3 text-muted-foreground">
              {checkoutUrl ? (
                <>
                  <p>Suas informações foram recebidas para que o Leonardo possa montar o treino.</p>
                  <div className="rounded-2xl border border-primary/25 bg-primary/10 p-4 text-foreground">
                    <p className="font-semibold">Próximo passo</p>
                    <p className="mt-1 text-sm text-muted-foreground">
                      Falta confirmar a contratação dos três meses. O pagamento é único: <strong>R$ 97 total</strong> pelo período completo.
                    </p>
                  </div>
                  <p>Após o pagamento, a avaliação segue para preparação do treino personalizado.</p>
                  <a
                    className="btn-primary-lg inline-flex items-center justify-center"
                    href={checkoutUrl}
                    target="_blank"
                    rel="noreferrer"
                    onClick={() => trackEvent("PaymentButtonClicked", { leadToken: data.leadToken })}
                  >
                    Confirmar meus 3 meses de treino — R$ 97
                  </a>
                </>
              ) : null}
            </div>
          )}
          {status === "checkout_error" && (
            <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm">
              Não foi possível abrir o pagamento agora. Tente novamente em instantes.
            </div>
          )}
          {status === "error" && (
            <div className="rounded-xl border border-destructive/30 bg-destructive/10 p-4 text-sm">
              Não foi possível enviar agora. Atualize a página ou solicite um novo link pelo WhatsApp.
            </div>
          )}
        </div>
      </section>

      <section className="px-5 pb-6 max-w-xl w-full mx-auto">
        <div className="text-xs uppercase tracking-wider text-muted-foreground mb-3 pl-1">
          Resumo da tua avaliação
        </div>
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
          {summaryCards.map(({ icon: Icon, k, v }) => (
            <div
              key={k}
              className="rounded-2xl border border-border bg-card p-4 flex items-start gap-3"
            >
              <div className="w-9 h-9 rounded-lg bg-primary/10 border border-primary/20 flex items-center justify-center shrink-0">
                <Icon className="w-4 h-4 text-primary" />
              </div>
              <div className="min-w-0">
                <div className="text-[11px] uppercase tracking-wider text-muted-foreground">
                  {k}
                </div>
                <div className="font-medium text-sm mt-0.5 break-words">{v}</div>
              </div>
            </div>
          ))}
        </div>
      </section>

      <section className="px-5 pb-12 max-w-xl w-full mx-auto space-y-3">
        <div className="flex items-start gap-2 text-xs text-muted-foreground">
          <ShieldCheck className="w-4 h-4 text-primary shrink-0 mt-0.5" />
          <span>
            Suas respostas ficam vinculadas ao atendimento iniciado pelo WhatsApp.
          </span>
        </div>
        <p className="text-[11px] text-muted-foreground text-center pt-2 leading-relaxed">
          Este plano não substitui avaliação médica. Se você tem dor, lesão ou
          limitação importante, informe corretamente na avaliação e procure
          orientação profissional quando necessário.
        </p>
      </section>
    </div>
  );
}

import { ArrowRight, User } from "lucide-react";

type Props = { onStart: () => void; onReset?: () => void; hasProgress?: boolean };

export function Welcome({ onStart, onReset, hasProgress }: Props) {
  return (
    <div className="min-h-[100dvh] flex flex-col">
      <main className="flex-1 flex flex-col justify-center px-5 py-10 max-w-xl w-full mx-auto">
        <span className="text-[11px] font-semibold uppercase tracking-[0.22em] text-primary mb-4">
          Continuando do WhatsApp · Avaliação
        </span>

        <h1 className="font-heading text-4xl sm:text-5xl leading-[1.05] mb-3">
          Método Híbrido <span className="brand-mark">LeoFT</span>
        </h1>
        <p className="text-muted-foreground text-base sm:text-lg mb-8">
          Teu plano personalizado começa com esta avaliação.
        </p>

        {/* Placeholder para foto do Leonardo — substituir pela imagem real */}
        <div className="flex items-center gap-4 rounded-2xl border border-border bg-card p-4 mb-8">
          <div className="w-14 h-14 rounded-full bg-secondary flex items-center justify-center overflow-hidden shrink-0">
            <User className="w-6 h-6 text-muted-foreground" />
          </div>
          <div className="min-w-0">
            <div className="font-medium text-sm">Leonardo Ferrioli Telles</div>
            <div className="text-xs text-muted-foreground">Personal Trainer</div>
          </div>
        </div>

        <div className="space-y-3 text-sm text-muted-foreground mb-10">
          <p>
            Responda algumas perguntas rápidas para o Leonardo entender teu
            objetivo, tua rotina e o melhor caminho para montar teu treino de
            3 meses.
          </p>
          <p className="text-foreground">
            Não é treino genérico. É um plano criado com base nas tuas respostas.
          </p>
        </div>
      </main>

      <footer className="px-5 pb-8 pt-4 max-w-xl w-full mx-auto sticky bottom-0 bg-gradient-to-t from-background via-background to-transparent">
        <button
          className="btn-primary-lg flex items-center justify-center gap-2"
          onClick={onStart}
        >
          Começar avaliação
          <ArrowRight className="w-4 h-4" />
        </button>
        <p className="text-center text-xs text-muted-foreground mt-3">
          Leva menos de 2 minutos
        </p>
        {hasProgress && onReset && (
          <div className="text-center mt-2">
            <button
              type="button"
              onClick={() => {
                if (confirm("Reiniciar avaliação? As respostas salvas serão apagadas.")) onReset();
              }}
              className="text-[11px] text-muted-foreground/70 hover:text-muted-foreground underline underline-offset-2"
            >
              Reiniciar avaliação
            </button>
          </div>
        )}
      </footer>
    </div>
  );
}
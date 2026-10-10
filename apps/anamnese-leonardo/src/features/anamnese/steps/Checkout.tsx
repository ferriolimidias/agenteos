import { ArrowLeft, Lock, ShieldCheck } from "lucide-react";
import { useState } from "react";
import type { AnamneseData } from "../lib/types";
import { createCheckoutPreference } from "../lib/api";

// ============================================================================
//  Checkout — Método Híbrido LeoFT
//  Área preparada para integração com o Mercado Pago (Payment Brick ou
//  checkout embedado retornado pelo backend). Nunca colocar access token
//  secreto no front-end. Apenas paymentPreferenceId + publicKey (públicos)
//  devem chegar aqui via createCheckoutPreference().
// ============================================================================

type Props = {
  data: AnamneseData;
  onBack: () => void;
  onCheckoutClick: () => void;
};

export function Checkout({ data, onBack, onCheckoutClick }: Props) {
  const [msg, setMsg] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const handleMockPay = async () => {
    setLoading(true);
    onCheckoutClick();
    // TODO: quando o backend estiver pronto, receber:
    //   { paymentPreferenceId, publicKey, payer, external_reference,
    //     anamnese_session_id, leadToken }
    // e inicializar o Mercado Pago Payment Brick aqui.
    await createCheckoutPreference(data);
    setLoading(false);
    setMsg("Integração de pagamento será conectada em breve.");
  };

  return (
    <div className="min-h-[100dvh] flex flex-col">
      <header className="px-5 pt-5 pb-3 max-w-xl w-full mx-auto">
        <button
          onClick={onBack}
          className="p-2 -ml-2 rounded-full text-muted-foreground hover:text-foreground transition-colors"
          aria-label="Voltar"
        >
          <ArrowLeft className="w-5 h-5" />
        </button>
      </header>

      <main className="flex-1 px-5 py-4 max-w-xl w-full mx-auto">
        <div className="animate-step-in">
          <h1 className="font-heading text-3xl sm:text-4xl leading-tight mb-3">
            Finalize para liberar a criação do teu plano
          </h1>
          <p className="text-muted-foreground mb-6">
            O investimento é <strong className="text-foreground">R$ 97</strong>{" "}
            pelos 3 meses. Depois do pagamento confirmado, o Leonardo monta teu
            plano personalizado com base na tua avaliação e libera pelo
            aplicativo em até 24 horas.
          </p>

          {/* ================================================================
              Placeholder do Mercado Pago Payment Brick.
              Substituir este bloco pelo <div id="paymentBrick_container" />
              e inicializar o SDK JS do Mercado Pago com a publicKey +
              preferenceId retornados pelo backend em /checkout/create.
          ================================================================ */}
          <div className="rounded-2xl border border-border bg-card p-5 mb-4">
            <div className="flex items-center justify-between mb-4">
              <div>
                <div className="text-xs uppercase tracking-wider text-muted-foreground">
                  Checkout Mercado Pago
                </div>
                <div className="font-heading text-2xl mt-1">R$ 97,00</div>
                <div className="text-xs text-muted-foreground">
                  Pagamento único · 3 meses de plano
                </div>
              </div>
              <div className="w-12 h-12 rounded-xl bg-primary/10 border border-primary/30 flex items-center justify-center">
                <Lock className="w-5 h-5 text-primary" />
              </div>
            </div>

            <div className="rounded-xl border border-dashed border-border bg-background/50 py-8 text-center text-xs text-muted-foreground">
              Área reservada para o Mercado Pago Payment Brick
            </div>
          </div>

          <div className="flex items-center gap-2 text-xs text-muted-foreground mb-6">
            <ShieldCheck className="w-4 h-4 text-primary shrink-0" />
            <span>Ambiente seguro. Nenhum dado sensível de cartão é armazenado.</span>
          </div>

          {msg && (
            <div className="rounded-xl border border-primary/30 bg-primary/10 p-4 text-sm mb-4">
              {msg}
            </div>
          )}
        </div>
      </main>

      <footer className="px-5 pb-8 pt-4 max-w-xl w-full mx-auto sticky bottom-0 bg-gradient-to-t from-background via-background to-transparent">
        <button
          className="btn-primary-lg flex items-center justify-center gap-2"
          onClick={handleMockPay}
          disabled={loading}
        >
          {loading ? "Processando…" : "Finalizar pagamento"}
        </button>
        <p className="text-center text-xs text-muted-foreground mt-3">
          Ao continuar, você concorda com os termos do Método Híbrido LeoFT.
        </p>
      </footer>
    </div>
  );
}
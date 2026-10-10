import { ArrowLeft, ArrowRight } from "lucide-react";
import { CSSProperties, ReactNode, useEffect, useState } from "react";
import { ProgressBar } from "./ProgressBar";

type Props = {
  step: number;
  totalSteps: number;
  title: string;
  subtitle?: string;
  onBack?: () => void;
  onContinue?: () => void;
  continueDisabled?: boolean;
  continueLabel?: string;
  showContinue?: boolean;
  children: ReactNode;
  footer?: ReactNode;
};

export function StepShell({
  step,
  totalSteps,
  title,
  subtitle,
  onBack,
  onContinue,
  continueDisabled,
  continueLabel = "Continuar",
  showContinue = true,
  children,
  footer,
}: Props) {
  const keyboardOffset = useKeyboardOffset();
  const viewportStyle = {
    "--keyboard-offset": `${keyboardOffset}px`,
  } as CSSProperties;

  return (
    <div className="anamnese-viewport min-h-[100dvh] flex flex-col" style={viewportStyle}>
      <header className="px-5 pt-5 pb-3 max-w-xl w-full mx-auto">
        <div className="flex items-center gap-3 mb-4">
          {onBack ? (
            <button
              onClick={onBack}
              className="p-2 -ml-2 rounded-full text-muted-foreground hover:text-foreground transition-colors"
              aria-label="Voltar"
            >
              <ArrowLeft className="w-5 h-5" />
            </button>
          ) : (
            <div className="w-9" />
          )}
          <span className="text-[11px] font-semibold uppercase tracking-[0.18em] text-muted-foreground">
            Método Híbrido <span className="text-primary">LeoFT</span>
          </span>
        </div>
        <ProgressBar current={step} total={totalSteps} />
      </header>

      <main className="anamnese-scroll flex-1 px-5 py-6 max-w-xl w-full mx-auto w-full">
        <div key={step} className="animate-step-in">
          <h1 className="font-heading text-2xl sm:text-3xl leading-tight mb-2">
            {title}
          </h1>
          {subtitle ? (
            <p className="text-muted-foreground mb-6 text-sm sm:text-base">
              {subtitle}
            </p>
          ) : (
            <div className="mb-6" />
          )}
          <div className="space-y-3">{children}</div>
        </div>
      </main>

      <footer className="anamnese-footer px-5 pt-4 max-w-xl w-full mx-auto sticky bg-gradient-to-t from-background via-background to-transparent">
        {footer}
        {showContinue && onContinue && (
          <button
            className="btn-primary-lg flex items-center justify-center gap-2"
            onClick={onContinue}
            disabled={continueDisabled}
          >
            {continueLabel}
            <ArrowRight className="w-4 h-4" />
          </button>
        )}
      </footer>
    </div>
  );
}

function useKeyboardOffset() {
  const [offset, setOffset] = useState(0);

  useEffect(() => {
    if (typeof window === "undefined" || !window.visualViewport) return;

    const viewport = window.visualViewport;
    const update = () => {
      const viewportBottom = viewport.offsetTop + viewport.height;
      const keyboardOverlap = Math.max(0, window.innerHeight - viewportBottom);
      setOffset(Math.round(keyboardOverlap));
    };

    update();
    viewport.addEventListener("resize", update);
    viewport.addEventListener("scroll", update);

    return () => {
      viewport.removeEventListener("resize", update);
      viewport.removeEventListener("scroll", update);
    };
  }, []);

  return offset;
}

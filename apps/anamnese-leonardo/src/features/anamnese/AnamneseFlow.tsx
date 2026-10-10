import { useEffect, useState } from "react";
import { useAnamneseStore } from "./lib/store";
import { trackEvent } from "./lib/tracking";
import {
  completeAnamnese,
  fetchSession,
  saveAnswer,
  startAnamnese,
  type AnamneseSession,
} from "./lib/api";
import { StepShell } from "./components/StepShell";
import { ChoiceList, MultiChoiceList } from "./components/ChoiceList";
import { Welcome } from "./steps/Welcome";
import { Summary } from "./steps/Summary";

// Screens: 0 welcome, 1..18 perguntas, 19 tela final (resumo + venda + checkout)
const TOTAL_QUESTIONS = 18;
const FINAL_STEP = 19;

const GENDER_OPTS = [
  { label: "Homem", value: "homem" },
  { label: "Mulher", value: "mulher" },
  { label: "Prefiro não informar", value: "nao_informar" },
];

const GOAL_OPTS = [
  { label: "Emagrecer", value: "emagrecer" },
  { label: "Ganhar massa", value: "massa" },
  { label: "Melhorar condicionamento", value: "condicionamento" },
  { label: "Ganhar força", value: "forca" },
  { label: "Voltar a treinar com constância", value: "constancia" },
  { label: "Correr melhor", value: "correr" },
  { label: "Melhorar saúde e disposição", value: "saude" },
  { label: "Outro", value: "outro" },
];
const PRIORITY_OPTS = [
  { label: "Ter mais constância", value: "constancia" },
  { label: "Melhorar o corpo", value: "corpo" },
  { label: "Melhorar o fôlego", value: "folego" },
  { label: "Ganhar força", value: "forca" },
  { label: "Ter mais energia", value: "energia" },
  { label: "Sair do sedentarismo", value: "sedentarismo" },
  { label: "Ter um plano claro pra seguir", value: "plano" },
];
const TYPE_OPTS = [
  { label: "Musculação", value: "musculacao" },
  { label: "Corrida", value: "corrida" },
  { label: "Musculação + corrida", value: "hibrido" },
  { label: "Treino em casa", value: "casa" },
  { label: "Ainda não sei", value: "naosei" },
];
const LEVEL_OPTS = [
  { label: "Iniciante", value: "iniciante" },
  { label: "Já treinei, mas estou parado", value: "parado" },
  { label: "Intermediário", value: "intermediario" },
  { label: "Avançado", value: "avancado" },
];
const RECENT_OPTS = [
  { label: "Não estou treinando", value: "nenhum" },
  { label: "Treino às vezes", value: "asvezes" },
  { label: "1 a 2 vezes por semana", value: "1-2" },
  { label: "3 a 4 vezes por semana", value: "3-4" },
  { label: "5 vezes ou mais por semana", value: "5+" },
];
const DAYS_OPTS = ["2", "3", "4", "5", "6"].map((d) => ({
  label: `${d} dias`,
  value: d,
}));
const TIME_OPTS = [
  { label: "Até 30 minutos", value: "30" },
  { label: "30 a 45 minutos", value: "45" },
  { label: "45 a 60 minutos", value: "60" },
  { label: "Mais de 60 minutos", value: "60+" },
];
const PLACE_OPTS = [
  { label: "Academia", value: "academia" },
  { label: "Casa", value: "casa" },
  { label: "Rua/parque", value: "rua" },
  { label: "Academia + corrida", value: "academia_corrida" },
  { label: "Casa + corrida", value: "casa_corrida" },
  { label: "Ainda não sei", value: "naosei" },
];
const EQUIP_OPTS = [
  { label: "Academia completa", value: "academia_full" },
  { label: "Halteres", value: "halteres" },
  { label: "Elásticos", value: "elasticos" },
  { label: "Barra", value: "barra" },
  { label: "Esteira", value: "esteira" },
  { label: "Bicicleta ergométrica", value: "bike" },
  { label: "Nenhum equipamento", value: "nenhum" },
  { label: "Outros", value: "outros" },
];
const RUN_OPTS = [
  { label: "Não quero correr", value: "nao" },
  { label: "Quero começar do zero", value: "zero" },
  { label: "Já corro um pouco", value: "pouco" },
  { label: "Corro com frequência", value: "frequente" },
  { label: "Quero melhorar meu desempenho", value: "performance" },
];
const LIMIT_OPTS = [
  { label: "Não", value: "nao" },
  { label: "Joelho", value: "joelho" },
  { label: "Coluna", value: "coluna" },
  { label: "Ombro", value: "ombro" },
  { label: "Quadril", value: "quadril" },
  { label: "Tornozelo", value: "tornozelo" },
  { label: "Punho/cotovelo", value: "punho" },
  { label: "Outra", value: "outra" },
];
const CARE_OPTS = [
  { label: "Não", value: "nao" },
  { label: "Tenho pouco tempo", value: "tempo" },
  { label: "Tenho dificuldade com corrida", value: "corrida" },
  { label: "Tenho dificuldade com musculação", value: "musculacao" },
  { label: "Tenho baixa disposição", value: "disposicao" },
  { label: "Tenho alguma condição de saúde", value: "saude" },
  { label: "Outro", value: "outro" },
];
const DIFF_OPTS = [
  { label: "Falta de constância", value: "constancia" },
  { label: "Falta de direção", value: "direcao" },
  { label: "Falta de tempo", value: "tempo" },
  { label: "Não saber o que treinar", value: "oque" },
  { label: "Começar e parar", value: "parar" },
  { label: "Falta de motivação", value: "motivacao" },
  { label: "Medo de fazer errado", value: "medo" },
];
const COMMIT_OPTS = [
  { label: "Sim, quero começar", value: "sim" },
  { label: "Sim, mas preciso de algo possível pra minha rotina", value: "sim_possivel" },
  { label: "Ainda estou em dúvida", value: "duvida" },
];

export function AnamneseFlow() {
  const store = useAnamneseStore();
  const { data, updatePersonal, updateTraining, updateStatus, reset } = store;
  const [step, setStep] = useState(0);
  const [sessionState, setSessionState] = useState<"loading" | "valid" | "invalid">("loading");
  const [session, setSession] = useState<AnamneseSession | null>(null);

  const go = (n: number) => setStep((s) => Math.max(0, Math.min(FINAL_STEP, s + n)));
  const next = () => go(1);
  const back = () => go(-1);

  useEffect(() => {
    if (!data.leadToken) {
      setSessionState("invalid");
      setSession(null);
      return;
    }

    let active = true;
    setSessionState("loading");
    fetchSession(data.leadToken)
      .then((loaded) => {
        if (!active) return;
        setSession(loaded);
        setSessionState("valid");
      })
      .catch(() => {
        if (!active) return;
        setSession(null);
        setSessionState("invalid");
      });

    return () => {
      active = false;
    };
  }, [data.leadToken]);

  const handleStart = async () => {
    trackEvent("AnamneseStarted", { leadToken: data.leadToken });
    updateStatus({ anamneseStarted: true });
    await startAnamnese(data);
    setStep(1);
  };

  const handleFinalScreen = async () => {
    trackEvent("AnamneseCompleted");
    trackEvent("CompleteRegistration");
    const completedData = {
      ...data,
      status: {
        ...data.status,
        anamneseCompleted: true,
      },
    };
    updateStatus(completedData.status);
    return completeAnamnese(completedData);
  };

  if (sessionState === "loading") {
    return (
      <div className="min-h-[100dvh] flex items-center justify-center px-5">
        <div className="max-w-sm text-center">
          <div className="font-heading text-2xl mb-2">Carregando avaliação</div>
          <p className="text-sm text-muted-foreground">Validando teu link de acesso.</p>
        </div>
      </div>
    );
  }

  if (sessionState === "invalid") {
    return (
      <div className="min-h-[100dvh] flex items-center justify-center px-5">
        <div className="max-w-sm text-center">
          <div className="font-heading text-2xl mb-3">Link inválido ou expirado</div>
          <p className="text-sm text-muted-foreground">
            Solicite um novo link pelo WhatsApp.
          </p>
        </div>
      </div>
    );
  }

  // ---------- WELCOME ----------
  if (step === 0) {
    const hasProgress = Boolean(
      data.personalData.name ||
        data.trainingProfile.mainGoal,
    );
    return (
      <Welcome
        onStart={handleStart}
        onReset={() => {
          reset();
          setStep(0);
        }}
        hasProgress={hasProgress}
      />
    );
  }

  // ---------- SUMMARY + OFERTA + CHECKOUT (tela final única) ----------
  if (step >= FINAL_STEP) return <Summary data={data} session={session} onContinue={handleFinalScreen} />;

  // ---------- QUESTION STEPS ----------
  const commonShell = {
    step,
    totalSteps: TOTAL_QUESTIONS,
    onBack: back,
  };

  switch (step) {
    case 1:
      return (
        <StepShell
          {...commonShell}
          title="Qual é teu nome?"
          subtitle="Assim o Leonardo identifica tua avaliação corretamente."
          continueDisabled={!data.personalData.name.trim()}
          onContinue={() => {
            saveAnswer("name", data.personalData.name);
            next();
          }}
        >
          <input
            className="field-input"
            placeholder="Seu nome"
            autoFocus
            value={data.personalData.name}
            onChange={(e) => updatePersonal({ name: e.target.value })}
          />
        </StepShell>
      );

    case 2:
      return (
        <StepShell
          {...commonShell}
          title="Qual é tua idade?"
          continueDisabled={!data.personalData.age.trim()}
          onContinue={() => {
            saveAnswer("age", data.personalData.age);
            next();
          }}
        >
          <input
            className="field-input"
            placeholder="Ex: 32"
            inputMode="numeric"
            autoFocus
            value={data.personalData.age}
            onChange={(e) => updatePersonal({ age: e.target.value.replace(/\D/g, "") })}
          />
        </StepShell>
      );

    case 3:
      return (
        <StepShell
          {...commonShell}
          title="Para ajustar melhor o plano, qual opção representa você?"
          subtitle="Essa informação ajuda a personalizar melhor a estrutura do treino."
          showContinue={false}
        >
          <ChoiceList
            options={GENDER_OPTS}
            value={data.personalData.gender}
            onChange={(v) => updatePersonal({ gender: v })}
            autoAdvance={next}
          />
        </StepShell>
      );

    case 4:
      return (
        <StepShell
          {...commonShell}
          title="Qual é teu objetivo principal agora?"
          showContinue={false}
        >
          <ChoiceList
            options={GOAL_OPTS}
            value={data.trainingProfile.mainGoal}
            onChange={(v) => updateTraining({ mainGoal: v })}
            autoAdvance={next}
          />
        </StepShell>
      );

    case 5:
      return (
        <StepShell
          {...commonShell}
          title="O que é mais importante pra você neste momento?"
          subtitle="Isso ajuda a definir a direção principal do teu plano."
          showContinue={false}
        >
          <ChoiceList
            options={PRIORITY_OPTS}
            value={data.trainingProfile.goalPriority}
            onChange={(v) => updateTraining({ goalPriority: v })}
            autoAdvance={next}
          />
        </StepShell>
      );

    case 6:
      return (
        <StepShell
          {...commonShell}
          title="Você quer focar em qual tipo de treino?"
          showContinue={false}
        >
          <ChoiceList
            options={TYPE_OPTS}
            value={data.trainingProfile.trainingType}
            onChange={(v) => updateTraining({ trainingType: v })}
            autoAdvance={next}
          />
        </StepShell>
      );

    case 7:
      return (
        <StepShell
          {...commonShell}
          title="Como você se considera hoje?"
          subtitle="Não precisa responder perfeito. O importante é mostrar tua realidade de hoje."
          showContinue={false}
        >
          <ChoiceList
            options={LEVEL_OPTS}
            value={data.trainingProfile.currentLevel}
            onChange={(v) => updateTraining({ currentLevel: v })}
            autoAdvance={next}
          />
        </StepShell>
      );

    case 8:
      return (
        <StepShell
          {...commonShell}
          title="Hoje você está treinando?"
          showContinue={false}
        >
          <ChoiceList
            options={RECENT_OPTS}
            value={data.trainingProfile.recentTrainingStatus}
            onChange={(v) => updateTraining({ recentTrainingStatus: v })}
            autoAdvance={next}
          />
        </StepShell>
      );

    case 9:
      return (
        <StepShell
          {...commonShell}
          title="Quantos dias por semana você consegue treinar de forma realista?"
          subtitle="O melhor plano é aquele que cabe na tua rotina real."
          showContinue={false}
        >
          <ChoiceList
            options={DAYS_OPTS}
            value={data.trainingProfile.daysPerWeek}
            onChange={(v) => updateTraining({ daysPerWeek: v })}
            autoAdvance={next}
          />
        </StepShell>
      );

    case 10:
      return (
        <StepShell
          {...commonShell}
          title="Quanto tempo você tem por treino?"
          showContinue={false}
        >
          <ChoiceList
            options={TIME_OPTS}
            value={data.trainingProfile.timePerSession}
            onChange={(v) => updateTraining({ timePerSession: v })}
            autoAdvance={next}
          />
        </StepShell>
      );

    case 11:
      return (
        <StepShell
          {...commonShell}
          title="Onde você vai treinar?"
          showContinue={false}
        >
          <ChoiceList
            options={PLACE_OPTS}
            value={data.trainingProfile.trainingPlace}
            onChange={(v) => updateTraining({ trainingPlace: v })}
            autoAdvance={next}
          />
        </StepShell>
      );

    case 12: {
      const hasOther = data.trainingProfile.equipment.includes("outros");
      return (
        <StepShell
          {...commonShell}
          title="Você tem acesso a quais equipamentos?"
          subtitle="Pode marcar mais de uma opção."
          onContinue={next}
          continueDisabled={data.trainingProfile.equipment.length === 0}
        >
          <MultiChoiceList
            options={EQUIP_OPTS}
            value={data.trainingProfile.equipment}
            onChange={(v) => updateTraining({ equipment: v })}
          />
          {hasOther && (
            <input
              className="field-input mt-3"
              placeholder="Quais?"
              value={data.trainingProfile.equipmentOther}
              onChange={(e) => updateTraining({ equipmentOther: e.target.value })}
            />
          )}
        </StepShell>
      );
    }

    case 13:
      return (
        <StepShell
          {...commonShell}
          title="Sobre corrida, qual opção combina mais com você?"
          showContinue={false}
        >
          <ChoiceList
            options={RUN_OPTS}
            value={data.trainingProfile.runningExperience}
            onChange={(v) => updateTraining({ runningExperience: v })}
            autoAdvance={next}
          />
        </StepShell>
      );

    case 14: {
      const hasLimit =
        data.trainingProfile.limitations.length > 0 &&
        !(data.trainingProfile.limitations.length === 1 && data.trainingProfile.limitations[0] === "nao");
      return (
        <StepShell
          {...commonShell}
          title="Você tem alguma dor, lesão ou limitação importante?"
          subtitle="Se tiver mais de uma, pode marcar tudo o que se aplica."
          onContinue={next}
          continueDisabled={data.trainingProfile.limitations.length === 0}
          footer={
            <p className="text-[11px] text-muted-foreground text-center mb-3 px-4">
              Essas informações ajudam a adaptar o treino, mas não substituem
              avaliação médica.
            </p>
          }
        >
          <MultiChoiceList
            options={LIMIT_OPTS}
            value={data.trainingProfile.limitations}
            onChange={(v) => {
              // Se marcar "Não", limpa as outras. Se marcar outra, remove "Não".
              const last = v[v.length - 1];
              let next = v;
              if (last === "nao") next = ["nao"];
              else next = v.filter((x) => x !== "nao");
              updateTraining({ limitations: next });
            }}
          />
          {hasLimit && (
            <textarea
              className="field-input mt-3 min-h-[90px] resize-none"
              placeholder="Descreva rapidamente, se quiser."
              value={data.trainingProfile.limitationsDetails}
              onChange={(e) =>
                updateTraining({ limitationsDetails: e.target.value })
              }
            />
          )}
        </StepShell>
      );
    }

    case 15: {
      const needsDetails =
        data.trainingProfile.specialCare === "saude" ||
        data.trainingProfile.specialCare === "outro";
      return (
        <StepShell
          {...commonShell}
          title="Existe algo que o Leonardo precisa saber antes de montar teu treino?"
          onContinue={next}
          continueDisabled={
            !data.trainingProfile.specialCare ||
            (needsDetails && !data.trainingProfile.specialCareDetails.trim())
          }
        >
          <ChoiceList
            options={CARE_OPTS}
            value={data.trainingProfile.specialCare}
            onChange={(v) => updateTraining({ specialCare: v })}
          />
          {needsDetails && (
            <textarea
              className="field-input mt-3 min-h-[90px] resize-none"
              placeholder="Descreva rapidamente."
              value={data.trainingProfile.specialCareDetails}
              onChange={(e) =>
                updateTraining({ specialCareDetails: e.target.value })
              }
            />
          )}
        </StepShell>
      );
    }

    case 16:
      return (
        <StepShell
          {...commonShell}
          title="Se quiser, informe peso e altura para ajudar na personalização."
          subtitle="Essas informações são opcionais, mas ajudam a ajustar melhor o plano."
          onContinue={next}
          footer={
            <button
              type="button"
              onClick={next}
              className="w-full text-center text-sm text-muted-foreground hover:text-foreground transition-colors py-3 mb-1"
            >
              Pular esta etapa
            </button>
          }
        >
          <label className="text-xs text-muted-foreground pl-1">Peso (kg)</label>
          <input
            className="field-input"
            placeholder="Ex: 78"
            inputMode="decimal"
            value={data.trainingProfile.weight}
            onChange={(e) => updateTraining({ weight: e.target.value })}
          />
          <label className="text-xs text-muted-foreground pl-1 pt-2 block">
            Altura (cm)
          </label>
          <input
            className="field-input"
            placeholder="Ex: 178"
            inputMode="numeric"
            value={data.trainingProfile.height}
            onChange={(e) => updateTraining({ height: e.target.value })}
          />
        </StepShell>
      );

    case 17:
      return (
        <StepShell
          {...commonShell}
          title="Hoje, o que mais te atrapalha?"
          showContinue={false}
        >
          <ChoiceList
            options={DIFF_OPTS}
            value={data.trainingProfile.mainDifficulty}
            onChange={(v) => updateTraining({ mainDifficulty: v })}
            autoAdvance={next}
          />
        </StepShell>
      );

    case 18:
      return (
        <StepShell
          {...commonShell}
          title="Você está disposto a seguir um plano simples e organizado pelos próximos 3 meses?"
          subtitle="Não é sobre treinar perfeito. É sobre ter um caminho claro e continuar."
          showContinue={false}
        >
          <ChoiceList
            options={COMMIT_OPTS}
            value={data.trainingProfile.commitmentLevel}
            onChange={(v) => updateTraining({ commitmentLevel: v })}
            autoAdvance={() => setStep(FINAL_STEP)}
          />
        </StepShell>
      );

    default:
      return <Welcome onStart={handleStart} />;
  }
}

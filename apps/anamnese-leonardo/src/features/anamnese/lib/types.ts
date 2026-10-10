// Estrutura de dados da anamnese — pronta para envio ao backend (Hermes/AgentOS).
export type AnamneseData = {
  leadToken: string;
  source: {
    utm_source: string;
    utm_medium: string;
    utm_campaign: string;
    utm_content: string;
    utm_term: string;
    fbclid: string;
    fbc: string;
    fbp: string;
  };
  personalData: {
    name: string;
    age: string;
    gender: string;
    instagram: string;
  };
  trainingProfile: {
    mainGoal: string;
    goalPriority: string;
    trainingType: string;
    currentLevel: string;
    recentTrainingStatus: string;
    daysPerWeek: string;
    timePerSession: string;
    trainingPlace: string;
    equipment: string[];
    equipmentOther: string;
    runningExperience: string;
    limitations: string[];
    limitationsDetails: string;
    specialCare: string;
    specialCareDetails: string;
    weight: string;
    height: string;
    mainDifficulty: string;
    commitmentLevel: string;
  };
  status: {
    anamneseStarted: boolean;
    anamneseCompleted: boolean;
    checkoutStarted: boolean;
    paymentStatus: "pending" | "approved" | "failed";
  };
};

export const emptyAnamnese = (): AnamneseData => ({
  leadToken: "",
  source: {
    utm_source: "",
    utm_medium: "",
    utm_campaign: "",
    utm_content: "",
    utm_term: "",
    fbclid: "",
    fbc: "",
    fbp: "",
  },
  personalData: { name: "", age: "", gender: "", instagram: "" },
  trainingProfile: {
    mainGoal: "",
    goalPriority: "",
    trainingType: "",
    currentLevel: "",
    recentTrainingStatus: "",
    daysPerWeek: "",
    timePerSession: "",
    trainingPlace: "",
    equipment: [],
    equipmentOther: "",
    runningExperience: "",
    limitations: [],
    limitationsDetails: "",
    specialCare: "",
    specialCareDetails: "",
    weight: "",
    height: "",
    mainDifficulty: "",
    commitmentLevel: "",
  },
  status: {
    anamneseStarted: false,
    anamneseCompleted: false,
    checkoutStarted: false,
    paymentStatus: "pending",
  },
});

import type { AnamneseData } from "./types";

const API_BASE = "/api/public/anamnese/leoft";

export type AnamneseSession = {
  ok: boolean;
  slug: string;
  oferta_nome: string;
  lead_nome?: string | null;
  checkout?: { url: string; provider?: string } | null;
  status: "valid";
};

export async function fetchSession(leadToken: string): Promise<AnamneseSession> {
  const response = await fetch(`${API_BASE}/session?token=${encodeURIComponent(leadToken)}`, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  if (!response.ok) {
    throw new Error("invalid_session");
  }
  return response.json();
}

export async function startAnamnese(data: Partial<AnamneseData>) {
  return { ok: true, leadTokenPresent: Boolean(data.leadToken) };
}

export async function saveAnswer(field: string, value: string) {
  return { ok: true, field, valuePresent: Boolean(value) };
}

export async function completeAnamnese(data: AnamneseData) {
  const response = await fetch(`${API_BASE}/respostas`, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      token: data.leadToken,
      respostas: {
        personalData: data.personalData,
        trainingProfile: data.trainingProfile,
        source: data.source,
        status: data.status,
      },
      metadata: {
        user_agent: typeof navigator !== "undefined" ? navigator.userAgent : "",
        submitted_at_client: new Date().toISOString(),
      },
    }),
  });
  if (!response.ok) {
    throw new Error("submit_failed");
  }
  return response.json() as Promise<{
    ok: boolean;
    checkout?: { url: string; provider?: string } | null;
    checkout_error?: string | null;
  }>;
}

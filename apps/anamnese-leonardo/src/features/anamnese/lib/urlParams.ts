import type { AnamneseData } from "./types";

// Captura parâmetros da URL enviados pelo AgentOS/Meta e retorna leadToken + source.
// O parâmetro principal é `t` (também aceita `token` / `lead_token`).
export function readUrlParams(): {
  leadToken: string;
  source: AnamneseData["source"];
} {
  if (typeof window === "undefined") {
    return {
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
    };
  }
  const p = new URLSearchParams(window.location.search);
  const leadToken =
    p.get("t") || p.get("token") || p.get("lead_token") || "";
  return {
    leadToken,
    source: {
      utm_source: p.get("utm_source") || "",
      utm_medium: p.get("utm_medium") || "",
      utm_campaign: p.get("utm_campaign") || "",
      utm_content: p.get("utm_content") || "",
      utm_term: p.get("utm_term") || "",
      fbclid: p.get("fbclid") || "",
      fbc: p.get("fbc") || "",
      fbp: p.get("fbp") || "",
    },
  };
}
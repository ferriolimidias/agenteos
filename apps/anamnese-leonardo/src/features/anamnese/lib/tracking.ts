// Placeholder de tracking. Futuramente conectar Meta Pixel / CAPI via backend.
// Nunca enviar leadToken / dados PII direto do browser em produção.
export type TrackEventName =
  | "AnamneseStarted"
  | "AnamneseCompleted"
  | "PaymentScreenViewed"
  | "PaymentButtonClicked"
  | "CompleteRegistration";

const EVENT_MAP: Record<TrackEventName, string> = {
  AnamneseStarted: "anamnese_started",
  AnamneseCompleted: "anamnese_completed",
  PaymentScreenViewed: "payment_screen_viewed",
  PaymentButtonClicked: "payment_button_clicked",
  CompleteRegistration: "anamnese_completed",
};

export function trackEvent(name: TrackEventName, payload?: Record<string, unknown>) {
  if (name === "AnamneseCompleted" || name === "CompleteRegistration") return;
  const token = typeof payload?.leadToken === "string" ? payload.leadToken : "";
  if (!token) return;
  fetch("/api/public/anamnese/leoft/event", {
    method: "POST",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({
      token,
      event_type: EVENT_MAP[name],
      metadata: { source: "browser" },
    }),
    keepalive: true,
  }).catch(() => undefined);
}

import type {
  CitationRef, HealthResponse, Language, SessionSummary,
} from "@/types";

const BASE = ""; // same-origin in production; vite proxies in dev

export async function fetchHealth(): Promise<HealthResponse> {
  const r = await fetch(`${BASE}/api/health`);
  if (!r.ok) throw new Error(`health ${r.status}`);
  return r.json();
}

export async function fetchSessions(): Promise<SessionSummary[]> {
  const r = await fetch(`${BASE}/api/sessions`);
  if (!r.ok) throw new Error(`sessions ${r.status}`);
  const data = await r.json();
  return data.sessions;
}

export interface SendFeedback {
  session_id: string;
  message_id?: string;
  thumbs?: "UP" | "DOWN";
  rating?: number;
  reason_codes?: string[];
  free_text?: string;
}

export async function sendFeedback(b: SendFeedback): Promise<{ feedback_id: string }> {
  const r = await fetch(`${BASE}/api/feedback`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(b),
  });
  if (!r.ok) throw new Error(`feedback ${r.status}`);
  return r.json();
}

export interface ChatStreamRequest {
  session_id?: string;
  claim_id?: string;
  message: string;
  language: Language;
  confirmations?: string[];
}

export interface ChatStreamHandle {
  abort: () => void;
  sessionId: string;
}

// Citation shape is referenced here so the API contract stays explicit
// for the consumer hook.
export type _CitationContractMarker = CitationRef;

export function chatStreamUrl(): string {
  return `${BASE}/api/chat`;
}

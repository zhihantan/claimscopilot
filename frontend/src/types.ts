// Mirrors backend/schemas.py. Keep in sync.

export type DecisionClass =
  | "APPROVE" | "PARTIAL_APPROVE" | "DENY"
  | "REQUEST_DOCS" | "UPDATE_STATUS" | "UNDETERMINED";

export type Confidence = "LOW" | "MED" | "HIGH";

export type Language = "en" | "es" | "ja";

export type Role = "user" | "assistant" | "system";

export interface CitationRef {
  kind: "policy" | "kb" | "claim";
  label: string;
  ref: string;
}

export interface PlanItem {
  tool: string;
  args: Record<string, unknown>;
  why: string;
}

// SSE event shapes

export type SSEEvent =
  | { event: "session.start"; trace_id: string; model: string; ts: string }
  | { event: "plan"; plan: PlanItem[]; stop_if_enough: boolean }
  | { event: "tool.start"; call_id: string; tool: string; args: Record<string, unknown> }
  | { event: "tool.end"; call_id: string; tool: string; result_preview: string;
       latency_ms: number; error: string | null }
  | { event: "reflect"; decision: "more" | "done"; note: string }
  | { event: "token"; delta: string }
  | { event: "citation"; citation: CitationRef }
  | { event: "error"; code: string; message: string; recoverable: boolean }
  | { event: "done"; trace_id: string; latency_ms_total: number; cost_usd: number;
       fallback_step: number; decision_class: DecisionClass; confidence: Confidence };

// UI message model

export interface ToolInvocation {
  call_id: string;
  tool: string;
  args: Record<string, unknown>;
  state: "running" | "ok" | "error";
  result_preview?: string;
  latency_ms?: number;
  error?: string | null;
}

export interface ChatMessage {
  id: string;
  role: Role;
  content: string;
  language?: Language;
  citations: CitationRef[];
  plan?: PlanItem[];
  tools: ToolInvocation[];
  trace_id?: string;
  latency_ms?: number;
  cost_usd?: number;
  fallback_step?: number;
  decision_class?: DecisionClass;
  confidence?: Confidence;
  streaming?: boolean;
  error?: { code: string; message: string };
  created_at: string;
}

export interface SessionSummary {
  session_id: string;
  claim_id: string | null;
  title: string;
  started_at: string;
  last_activity_at: string;
  message_count: number;
  status: "OPEN" | "CLOSED" | "ABANDONED";
}

export interface HealthResponse {
  status: "ok" | "degraded";
  agent_version: string;
  region: string;
  upstream: Record<string, string>;
}

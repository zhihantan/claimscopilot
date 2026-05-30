import { useCallback, useReducer, useRef } from "react";
import { createParser, type EventSourceMessage } from "eventsource-parser";
import { chatStreamUrl } from "@/api/client";
import { uid } from "@/lib/utils";
import type {
  ChatMessage, Language, SSEEvent, ToolInvocation,
} from "@/types";

// ---------- reducer ----------

type State = {
  sessionId: string | undefined;
  messages: ChatMessage[];
  streaming: boolean;
};

type Action =
  | { type: "send"; userText: string; language: Language }
  | { type: "begin"; sessionId: string }
  | { type: "event"; event: SSEEvent }
  | { type: "end" }
  | { type: "fail"; code: string; message: string }
  | { type: "reset"; sessionId?: string }
  | { type: "hydrate"; messages: ChatMessage[]; sessionId: string };

function activeAssistant(state: State): ChatMessage | undefined {
  for (let i = state.messages.length - 1; i >= 0; i--) {
    const m = state.messages[i];
    if (m.role === "assistant" && m.streaming) return m;
  }
  return undefined;
}

function patchActive(state: State, patch: Partial<ChatMessage>): State {
  const idx = state.messages.findLastIndex(
    (m) => m.role === "assistant" && m.streaming,
  );
  if (idx < 0) return state;
  const next = state.messages.slice();
  next[idx] = { ...next[idx], ...patch };
  return { ...state, messages: next };
}

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "reset":
      return { sessionId: action.sessionId, messages: [], streaming: false };
    case "hydrate":
      return { sessionId: action.sessionId, messages: action.messages, streaming: false };
    case "send": {
      const userMsg: ChatMessage = {
        id: uid(),
        role: "user",
        content: action.userText,
        language: action.language,
        citations: [], tools: [],
        created_at: new Date().toISOString(),
      };
      const assistantMsg: ChatMessage = {
        id: uid(),
        role: "assistant",
        content: "",
        citations: [], tools: [],
        streaming: true,
        created_at: new Date().toISOString(),
      };
      return {
        ...state,
        messages: [...state.messages, userMsg, assistantMsg],
        streaming: true,
      };
    }
    case "begin":
      return { ...state, sessionId: action.sessionId };
    case "event": {
      const ev = action.event;
      const active = activeAssistant(state);
      if (!active) return state;
      switch (ev.event) {
        case "session.start":
          return patchActive(state, { trace_id: ev.trace_id });
        case "plan":
          return patchActive(state, { plan: ev.plan });
        case "tool.start": {
          const t: ToolInvocation = {
            call_id: ev.call_id, tool: ev.tool, args: ev.args, state: "running",
          };
          return patchActive(state, { tools: [...active.tools, t] });
        }
        case "tool.end": {
          const tools = active.tools.map((t) =>
            t.call_id === ev.call_id
              ? {
                  ...t,
                  state: (ev.error ? "error" : "ok") as ToolInvocation["state"],
                  result_preview: ev.result_preview,
                  latency_ms: ev.latency_ms,
                  error: ev.error,
                }
              : t,
          );
          return patchActive(state, { tools });
        }
        case "reflect":
          return state;
        case "token":
          return patchActive(state, { content: active.content + ev.delta });
        case "citation":
          return patchActive(state, {
            citations: [...active.citations, ev.citation],
          });
        case "error":
          return patchActive(state, {
            error: { code: ev.code, message: ev.message },
            streaming: false,
          });
        case "done":
          return patchActive(state, {
            streaming: false,
            latency_ms: ev.latency_ms_total,
            cost_usd: ev.cost_usd,
            fallback_step: ev.fallback_step,
            decision_class: ev.decision_class,
            confidence: ev.confidence,
            trace_id: ev.trace_id,
          });
      }
      return state;
    }
    case "end":
      return { ...patchActive(state, { streaming: false }), streaming: false };
    case "fail":
      return {
        ...patchActive(state, {
          streaming: false,
          error: { code: action.code, message: action.message },
        }),
        streaming: false,
      };
  }
}

// ---------- hook ----------

export interface UseChatStreamArgs {
  claimId?: string;
  language: Language;
  onUnrecoverableError?: (code: string, message: string) => void;
}

export function useChatStream(args: UseChatStreamArgs) {
  const [state, dispatch] = useReducer(reducer, {
    sessionId: undefined, messages: [], streaming: false,
  });
  const abortRef = useRef<AbortController | null>(null);

  const send = useCallback(
    async (text: string, opts?: { confirmations?: string[] }) => {
      if (state.streaming) return;
      dispatch({ type: "send", userText: text, language: args.language });
      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const resp = await fetch(chatStreamUrl(), {
          method: "POST",
          headers: { "content-type": "application/json", accept: "text/event-stream" },
          credentials: "include",
          signal: controller.signal,
          body: JSON.stringify({
            session_id: state.sessionId,
            claim_id: args.claimId,
            message: text,
            language: args.language,
            confirmations: opts?.confirmations ?? [],
          }),
        });
        if (!resp.ok || !resp.body) {
          throw new Error(`stream HTTP ${resp.status}`);
        }
        const newSid = resp.headers.get("x-session-id");
        if (newSid && newSid !== state.sessionId) {
          dispatch({ type: "begin", sessionId: newSid });
        }
        const parser = createParser({
          onEvent(msg: EventSourceMessage) {
            try {
              const data = JSON.parse(msg.data) as SSEEvent;
              dispatch({ type: "event", event: data });
              if (data.event === "error" && !data.recoverable) {
                args.onUnrecoverableError?.(data.code, data.message);
              }
            } catch {
              /* ignore malformed frame */
            }
          },
        });
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          parser.feed(decoder.decode(value, { stream: true }));
        }
        dispatch({ type: "end" });
      } catch (e: unknown) {
        if ((e as DOMException)?.name === "AbortError") {
          dispatch({ type: "end" });
        } else {
          const msg = e instanceof Error ? e.message : "Unknown stream error";
          dispatch({ type: "fail", code: "STREAM_FAIL", message: msg });
          args.onUnrecoverableError?.("STREAM_FAIL", msg);
        }
      }
    },
    [args, state.streaming, state.sessionId],
  );

  const abort = useCallback(() => {
    abortRef.current?.abort();
  }, []);

  const reset = useCallback((sessionId?: string) => {
    abortRef.current?.abort();
    dispatch({ type: "reset", sessionId });
  }, []);

  return {
    sessionId: state.sessionId,
    messages: state.messages,
    streaming: state.streaming,
    send,
    abort,
    reset,
  };
}

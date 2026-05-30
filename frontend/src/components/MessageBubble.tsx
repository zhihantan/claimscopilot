import { useMemo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Check, Copy, ThumbsDown, ThumbsUp, Sparkles, AlertOctagon,
} from "lucide-react";
import { toast } from "sonner";
import { sendFeedback } from "@/api/client";
import { cn, copyToClipboard, decisionTone, formatCost, formatLatency } from "@/lib/utils";
import type { ChatMessage } from "@/types";
import { CitationChip } from "./CitationChip";
import { PlanCard } from "./PlanCard";
import { ToolCallCard } from "./ToolCallCard";
import { TraceLink } from "./TraceLink";

const DECISION_TAG_RE =
  /<decision\s+class="(\w+)"\s+confidence="(\w+)"\s*\/>/i;

export function MessageBubble({
  m, sessionId,
}: { m: ChatMessage; sessionId?: string }) {
  const isUser = m.role === "user";
  const visibleContent = useMemo(
    () => m.content.replace(DECISION_TAG_RE, "").trim(),
    [m.content],
  );

  const onThumbs = async (thumbs: "UP" | "DOWN") => {
    if (!sessionId) return;
    try {
      await sendFeedback({ session_id: sessionId, message_id: m.id, thumbs });
      toast.success(`Feedback recorded — ${thumbs === "UP" ? "thanks!" : "noted"}`);
    } catch (e) {
      toast.error("Could not save feedback");
    }
  };

  const onCopy = async () => {
    if (await copyToClipboard(visibleContent)) {
      toast.success("Copied to clipboard");
    }
  };

  if (isUser) {
    return (
      <div className="flex justify-end px-6 py-2">
        <div className="max-w-[78%] rounded-xl rounded-tr-md bg-brand px-3.5 py-2 text-[14px] text-white shadow-card">
          {m.content}
        </div>
      </div>
    );
  }

  const tone = decisionTone(m.decision_class);

  return (
    <div className="group px-6 py-3">
      <div className="flex max-w-[88%] flex-col gap-2">
        <div className="flex items-center gap-2">
          <div className="grid h-6 w-6 place-items-center rounded-md bg-brand/10">
            <Sparkles className="h-3.5 w-3.5 text-brand" />
          </div>
          <span className="text-[12px] font-semibold">ClaimsCopilot</span>
          {m.streaming && (
            <span className="text-[11px] text-muted animate-pulse-fade">
              thinking…
            </span>
          )}
          {!m.streaming && m.decision_class && (
            <span
              className={cn(
                "ml-1 inline-flex items-center gap-1 rounded-full px-1.5 py-0.5 text-[10.5px] font-semibold ring-1",
                tone.bg, tone.text, tone.ring,
              )}
            >
              {tone.label}
              {m.confidence && (
                <span className="opacity-70">· {m.confidence}</span>
              )}
            </span>
          )}
        </div>

        {m.plan && m.plan.length > 0 && <PlanCard plan={m.plan} />}

        {m.tools.length > 0 && (
          <div className="space-y-1.5">
            {m.tools.map((t) => (
              <ToolCallCard key={t.call_id} t={t} />
            ))}
          </div>
        )}

        <div
          className={cn(
            "rounded-xl rounded-tl-md border border-line bg-surface px-4 py-3 shadow-card",
            m.streaming && "border-brand/30",
          )}
        >
          {m.error && (
            <div className="mb-2 flex items-start gap-1.5 rounded bg-danger/10 px-2 py-1.5 text-[12px] text-danger">
              <AlertOctagon className="h-3.5 w-3.5 shrink-0" />
              <div>
                <div className="font-semibold">{m.error.code}</div>
                <div>{m.error.message}</div>
              </div>
            </div>
          )}
          <article className="prose-claims">
            {visibleContent ? (
              <ReactMarkdown remarkPlugins={[remarkGfm]}>
                {visibleContent}
              </ReactMarkdown>
            ) : m.streaming ? (
              <div className="space-y-2 py-1">
                <div className="h-3 w-1/3 animate-shimmer rounded bg-gradient-to-r from-sunken via-line to-sunken bg-[length:400px]" />
                <div className="h-3 w-4/5 animate-shimmer rounded bg-gradient-to-r from-sunken via-line to-sunken bg-[length:400px]" />
                <div className="h-3 w-2/3 animate-shimmer rounded bg-gradient-to-r from-sunken via-line to-sunken bg-[length:400px]" />
              </div>
            ) : (
              <div className="text-[13px] text-muted">No response.</div>
            )}
          </article>

          {m.citations.length > 0 && (
            <div className="mt-2 flex flex-wrap gap-1.5">
              {m.citations.map((c, i) => (
                <CitationChip key={`${c.ref}-${i}`} c={c} />
              ))}
            </div>
          )}
        </div>

        {!m.streaming && (
          <div className="flex items-center gap-2 pl-1 text-[11px] text-muted">
            <button
              onClick={onCopy}
              className="inline-flex items-center gap-1 rounded px-1 py-0.5 hover:bg-sunken"
              title="Copy"
            >
              <Copy className="h-3 w-3" /> Copy
            </button>
            <button
              onClick={() => onThumbs("UP")}
              className="inline-flex items-center gap-1 rounded px-1 py-0.5 hover:bg-sunken"
            >
              <ThumbsUp className="h-3 w-3" />
            </button>
            <button
              onClick={() => onThumbs("DOWN")}
              className="inline-flex items-center gap-1 rounded px-1 py-0.5 hover:bg-sunken"
            >
              <ThumbsDown className="h-3 w-3" />
            </button>
            <span className="mx-1">·</span>
            <span title="Total turn latency">
              <Check className="-mt-px mr-0.5 inline h-3 w-3" />
              {formatLatency(m.latency_ms)}
            </span>
            <span>·</span>
            <span title="Estimated cost">{formatCost(m.cost_usd)}</span>
            <span>·</span>
            <TraceLink traceId={m.trace_id} />
            {(m.fallback_step ?? 0) > 0 && (
              <span className="rounded bg-warn/10 px-1.5 py-0.5 font-mono text-[10px] text-warn">
                fallback step {m.fallback_step}
              </span>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

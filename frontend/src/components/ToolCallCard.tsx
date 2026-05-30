import { useState } from "react";
import {
  AlertTriangle, ChevronRight, CheckCircle2, Loader2, Wrench,
} from "lucide-react";
import { cn, formatLatency } from "@/lib/utils";
import type { ToolInvocation } from "@/types";

export function ToolCallCard({ t }: { t: ToolInvocation }) {
  const [open, setOpen] = useState(false);
  const argsPreview = compactJson(t.args);

  return (
    <div
      className={cn(
        "rounded-lg border bg-surface px-3 py-2 shadow-card transition-colors",
        t.state === "running" && "border-brand/30",
        t.state === "ok" && "border-line",
        t.state === "error" && "border-danger/40 bg-danger/[0.03]",
      )}
    >
      <button
        onClick={() => setOpen(!open)}
        className="flex w-full items-center gap-2 text-left"
      >
        {t.state === "running" && (
          <Loader2 className="h-3.5 w-3.5 animate-spin text-brand" />
        )}
        {t.state === "ok" && <CheckCircle2 className="h-3.5 w-3.5 text-ok" />}
        {t.state === "error" && <AlertTriangle className="h-3.5 w-3.5 text-danger" />}
        <Wrench className="h-3.5 w-3.5 text-muted" />
        <code className="font-mono text-[12px] font-medium text-ink">{t.tool}</code>
        <span className="ml-1 truncate text-[11px] text-muted">{argsPreview}</span>
        {t.latency_ms !== undefined && (
          <span className="ml-auto shrink-0 rounded bg-sunken px-1.5 py-0.5 font-mono text-[10px] text-muted">
            {formatLatency(t.latency_ms)}
          </span>
        )}
        <ChevronRight
          className={cn(
            "h-3.5 w-3.5 shrink-0 text-soft transition-transform",
            open && "rotate-90",
          )}
        />
      </button>
      {open && (
        <div className="mt-2 space-y-1.5 border-t border-line/70 pt-2">
          <div>
            <div className="text-[10px] font-semibold uppercase tracking-wide text-soft">
              Args
            </div>
            <pre className="mt-0.5 overflow-x-auto rounded bg-sunken px-2 py-1.5 font-mono text-[11px] leading-snug text-ink/90">
              {JSON.stringify(t.args, null, 2)}
            </pre>
          </div>
          {t.result_preview && (
            <div>
              <div className="text-[10px] font-semibold uppercase tracking-wide text-soft">
                Result preview
              </div>
              <pre className="mt-0.5 max-h-48 overflow-auto rounded bg-sunken px-2 py-1.5 font-mono text-[11px] leading-snug text-ink/90">
                {t.result_preview}
              </pre>
            </div>
          )}
          {t.error && (
            <div className="rounded bg-danger/10 px-2 py-1.5 text-[11px] text-danger">
              {t.error}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function compactJson(o: unknown): string {
  const s = JSON.stringify(o);
  if (!s) return "";
  return s.length > 90 ? s.slice(0, 90) + "…" : s;
}

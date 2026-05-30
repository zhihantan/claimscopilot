import { ExternalLink, Hash } from "lucide-react";
import * as Tooltip from "@radix-ui/react-tooltip";
import { copyToClipboard } from "@/lib/utils";
import { toast } from "sonner";

export function TraceLink({ traceId }: { traceId?: string }) {
  if (!traceId) return null;
  const short = traceId.slice(0, 8);
  const handle = async () => {
    if (await copyToClipboard(traceId)) {
      toast.success(`Trace ID copied — ${short}`);
    }
  };
  return (
    <Tooltip.Provider delayDuration={150}>
      <Tooltip.Root>
        <Tooltip.Trigger asChild>
          <button
            onClick={handle}
            className="inline-flex items-center gap-1 rounded bg-sunken px-1.5 py-0.5 font-mono text-[10px] text-muted hover:bg-line/60"
          >
            <Hash className="h-2.5 w-2.5" />
            {short}
            <ExternalLink className="h-2.5 w-2.5" />
          </button>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            sideOffset={4}
            className="rounded-md bg-ink px-2 py-1 text-[10.5px] text-white shadow-pop"
          >
            Copy MLflow trace ID
            <Tooltip.Arrow className="fill-ink" />
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}

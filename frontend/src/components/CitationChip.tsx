import * as Popover from "@radix-ui/react-popover";
import { BookOpen, FileText, ScrollText } from "lucide-react";
import { cn } from "@/lib/utils";
import type { CitationRef } from "@/types";

export function CitationChip({ c }: { c: CitationRef }) {
  const Icon =
    c.kind === "policy" ? ScrollText : c.kind === "kb" ? BookOpen : FileText;
  const tone =
    c.kind === "policy"
      ? "bg-brand/10 text-brand"
      : c.kind === "kb"
      ? "bg-info/10 text-info"
      : "bg-muted/10 text-muted";
  return (
    <Popover.Root>
      <Popover.Trigger asChild>
        <button
          className={cn(
            "inline-flex items-center gap-1 rounded-full px-2 py-0.5 font-mono text-[10.5px]",
            "transition-colors hover:brightness-95",
            tone,
          )}
        >
          <Icon className="h-3 w-3" />
          {c.label}
        </button>
      </Popover.Trigger>
      <Popover.Portal>
        <Popover.Content
          sideOffset={6}
          className="z-50 max-w-sm rounded-lg border border-line bg-surface p-3 text-[12px] shadow-pop"
        >
          <div className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-soft">
            {c.kind} citation
          </div>
          <div className="break-all font-mono text-[11px] text-ink">{c.ref}</div>
          <Popover.Arrow className="fill-white drop-shadow" />
        </Popover.Content>
      </Popover.Portal>
    </Popover.Root>
  );
}

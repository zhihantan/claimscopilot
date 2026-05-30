import { ListChecks } from "lucide-react";
import type { PlanItem } from "@/types";

export function PlanCard({ plan }: { plan: PlanItem[] }) {
  if (!plan || plan.length === 0) return null;
  return (
    <div className="rounded-lg border border-line bg-sunken/40 p-2.5">
      <div className="mb-1.5 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
        <ListChecks className="h-3 w-3" />
        Plan ({plan.length} tool{plan.length === 1 ? "" : "s"})
      </div>
      <ol className="space-y-1">
        {plan.map((p, i) => (
          <li
            key={`${p.tool}-${i}`}
            className="flex items-baseline gap-2 text-[12px] leading-snug"
          >
            <span className="grid h-4 w-4 shrink-0 place-items-center rounded-full bg-line text-[10px] font-bold text-ink/70">
              {i + 1}
            </span>
            <code className="font-mono text-[12px] text-ink">{p.tool}</code>
            <span className="text-muted">— {p.why}</span>
          </li>
        ))}
      </ol>
    </div>
  );
}

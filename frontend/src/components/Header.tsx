import { Activity, CircleDot, ShieldCheck } from "lucide-react";
import { cn } from "@/lib/utils";

interface Props {
  user?: { display_name?: string | null; email: string; role: string; country: string };
  claimId?: string;
  health?: "ok" | "degraded";
}

export function Header({ user, claimId, health }: Props) {
  return (
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface px-5">
      <div className="flex flex-col">
        <div className="text-[13px] font-semibold leading-tight">
          {claimId ? <>Claim <span className="font-mono">{claimId}</span></> : "Ask anything"}
        </div>
        <div className="text-[11px] text-muted">
          Assistive system · adjuster makes the final decision
        </div>
      </div>
      <div className="ml-auto flex items-center gap-3">
        <div
          className={cn(
            "flex items-center gap-1.5 rounded-full px-2 py-1 text-[11px]",
            health === "ok"
              ? "bg-ok/10 text-ok"
              : "bg-warn/10 text-warn",
          )}
        >
          {health === "ok" ? (
            <CircleDot className="h-3 w-3" />
          ) : (
            <Activity className="h-3 w-3" />
          )}
          {health === "ok" ? "All systems normal" : "Degraded"}
        </div>
        <div className="flex items-center gap-1.5 rounded-full bg-sunken px-2 py-1 text-[11px] text-ink/80">
          <ShieldCheck className="h-3 w-3 text-brand" />
          OBO authenticated
        </div>
        {user && (
          <div className="flex items-center gap-2 pl-1">
            <div className="grid h-7 w-7 place-items-center rounded-full bg-brand/10 text-[11px] font-bold text-brand">
              {(user.display_name || user.email).slice(0, 2).toUpperCase()}
            </div>
            <div className="hidden flex-col leading-tight md:flex">
              <span className="text-[12px] font-semibold">
                {user.display_name || user.email}
              </span>
              <span className="text-[10px] text-muted">
                {user.role} · {user.country}
              </span>
            </div>
          </div>
        )}
      </div>
    </header>
  );
}

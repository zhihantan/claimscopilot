import { useQuery } from "@tanstack/react-query";
import { CheckCircle2, MessagesSquare, PlusCircle, Sparkles } from "lucide-react";
import { fetchSessions } from "@/api/client";
import { cn, relativeTime } from "@/lib/utils";

const EXAMPLE_PROMPTS = [
  "Is the cracked screen covered? What's the excess?",
  "The customer has 3 claims in 6 months — what should I do?",
  "Draft a Spanish approval message for this customer.",
  "Find precedent claims for partial settlements on liquid damage.",
];

interface Props {
  activeSessionId?: string;
  onSelect: (sessionId: string) => void;
  onNew: () => void;
  onExamplePrompt: (prompt: string) => void;
  appVersion: string;
  region: string;
}

export function Sidebar({
  activeSessionId, onSelect, onNew, onExamplePrompt, appVersion, region,
}: Props) {
  const sessionsQ = useQuery({
    queryKey: ["sessions"],
    queryFn: fetchSessions,
  });

  return (
    <aside className="flex h-full w-[296px] flex-col border-r border-line bg-surface">
      <div className="flex items-center gap-2 border-b border-line px-4 py-3">
        <div className="grid h-7 w-7 place-items-center rounded-md bg-brand text-[12px] font-bold text-white">
          C
        </div>
        <div className="flex-1">
          <div className="text-[13px] font-semibold leading-tight">ClaimsCopilot</div>
          <div className="text-[11px] text-muted">
            {appVersion} · {region}
          </div>
        </div>
      </div>

      <button
        onClick={onNew}
        className={cn(
          "mx-3 mt-3 flex items-center gap-2 rounded-lg border border-line bg-sunken px-3 py-2",
          "text-[13px] font-medium text-ink transition-colors hover:bg-line/60",
          "focus:outline-none focus:shadow-focus",
        )}
      >
        <PlusCircle className="h-4 w-4 text-muted" />
        New session
      </button>

      <div className="mt-4 px-3 text-[11px] font-semibold uppercase tracking-wide text-soft">
        Sessions
      </div>

      <div className="mt-1 flex-1 overflow-y-auto px-2 pb-3">
        {sessionsQ.isLoading && (
          <div className="space-y-2 px-1 pt-2">
            {Array.from({ length: 5 }).map((_, i) => (
              <div
                key={i}
                className="h-12 animate-pulse-fade rounded-md bg-sunken"
              />
            ))}
          </div>
        )}
        {sessionsQ.data?.length === 0 && (
          <div className="px-2 pt-2 text-[12px] text-soft">
            No sessions yet. Start a new one.
          </div>
        )}
        <ul className="space-y-0.5">
          {sessionsQ.data?.map((s) => {
            const active = s.session_id === activeSessionId;
            return (
              <li key={s.session_id}>
                <button
                  onClick={() => onSelect(s.session_id)}
                  className={cn(
                    "group flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left",
                    "transition-colors",
                    active ? "bg-brand/[0.08]" : "hover:bg-sunken",
                  )}
                >
                  <MessagesSquare
                    className={cn(
                      "mt-0.5 h-3.5 w-3.5 shrink-0",
                      active ? "text-brand" : "text-soft",
                    )}
                  />
                  <div className="flex-1 overflow-hidden">
                    <div
                      className={cn(
                        "truncate text-[13px] leading-tight",
                        active ? "font-semibold text-ink" : "text-ink",
                      )}
                    >
                      {s.title}
                    </div>
                    <div className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted">
                      {s.status === "CLOSED" && (
                        <CheckCircle2 className="h-3 w-3 text-ok" />
                      )}
                      <span>{s.message_count} msg</span>
                      <span>·</span>
                      <span>{relativeTime(s.last_activity_at)}</span>
                    </div>
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      </div>

      <div className="border-t border-line p-3">
        <div className="mb-1 flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-wide text-soft">
          <Sparkles className="h-3 w-3" />
          Example prompts
        </div>
        <div className="space-y-1">
          {EXAMPLE_PROMPTS.map((p) => (
            <button
              key={p}
              onClick={() => onExamplePrompt(p)}
              className="w-full rounded-md bg-sunken/70 px-2 py-1.5 text-left text-[12px] leading-snug text-ink/90 hover:bg-line/60"
            >
              {p}
            </button>
          ))}
        </div>
      </div>
    </aside>
  );
}

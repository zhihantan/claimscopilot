import { useEffect, useRef } from "react";
import { Sparkles } from "lucide-react";
import { MessageBubble } from "./MessageBubble";
import type { ChatMessage } from "@/types";

interface Props {
  messages: ChatMessage[];
  sessionId?: string;
  emptyHint?: string;
}

export function ChatPane({ messages, sessionId, emptyHint }: Props) {
  const endRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  if (messages.length === 0) {
    return (
      <div className="flex h-full items-center justify-center">
        <div className="max-w-md text-center">
          <div className="mx-auto mb-3 grid h-10 w-10 place-items-center rounded-xl bg-brand/10">
            <Sparkles className="h-5 w-5 text-brand" />
          </div>
          <div className="text-[15px] font-semibold">
            Ask anything about this claim
          </div>
          <div className="mt-1 text-[13px] text-muted">
            {emptyHint ??
              "Coverage, excess, partner SLA, precedents, drafted customer messages — all grounded in policy wording and your claims data."}
          </div>
          <div className="mt-4 grid grid-cols-1 gap-2 text-left">
            {[
              "Is the cracked screen covered? What's the excess?",
              "Show me precedent for partial settlements on liquid damage.",
              "Draft an empathetic approval message for this customer.",
            ].map((p) => (
              <div
                key={p}
                className="rounded-lg border border-line bg-surface px-3 py-2 text-[13px] text-ink/90 shadow-card"
              >
                {p}
              </div>
            ))}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-y-auto">
      <div className="mx-auto max-w-3xl">
        {messages.map((m) => (
          <MessageBubble key={m.id} m={m} sessionId={sessionId} />
        ))}
        <div ref={endRef} className="h-2" />
      </div>
    </div>
  );
}

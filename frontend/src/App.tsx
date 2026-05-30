import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { fetchHealth } from "@/api/client";
import { Sidebar } from "@/components/Sidebar";
import { Header } from "@/components/Header";
import { ChatPane } from "@/components/ChatPane";
import { ChatInput } from "@/components/ChatInput";
import { useChatStream } from "@/hooks/useChatStream";
import type { Language } from "@/types";

// Stub identity. In production the App's auth dependency provides this via
// /api/me; for the workshop we display a friendly default.
const DEMO_USER = {
  email: "amy.adjuster@example.com",
  display_name: "Amy Adjuster",
  role: "ADJUSTER_L2",
  country: "GB",
};

export default function App() {
  const qc = useQueryClient();
  const [language, setLanguage] = useState<Language>("en");
  const [activeClaimId, setActiveClaimId] = useState<string | undefined>(undefined);
  const [activeSessionId, setActiveSessionId] = useState<string | undefined>();
  const [prefill, setPrefill] = useState<string | undefined>();

  const healthQ = useQuery({ queryKey: ["health"], queryFn: fetchHealth });

  const chat = useChatStream({
    claimId: activeClaimId,
    language,
    onUnrecoverableError: (code, message) =>
      toast.error(`${code}: ${message}`),
  });

  // Keep the sessions list fresh after sends finish.
  useEffect(() => {
    if (!chat.streaming) qc.invalidateQueries({ queryKey: ["sessions"] });
  }, [chat.streaming, qc]);

  // Wire a synthetic claim from a query string for demo purposes.
  useEffect(() => {
    const u = new URL(window.location.href);
    const cid = u.searchParams.get("claim");
    if (cid) setActiveClaimId(cid);
  }, []);

  const onSelect = (sid: string) => {
    chat.reset(sid);
    setActiveSessionId(sid);
  };
  const onNew = () => {
    chat.reset();
    setActiveSessionId(undefined);
  };

  // Prefilled state passes the prompt to ChatInput via key-trick.
  const inputKey = useMemo(() => `${activeSessionId ?? "new"}-${prefill ?? ""}`, [
    activeSessionId, prefill,
  ]);

  return (
    <div className="flex h-full w-full overflow-hidden bg-canvas">
      <Sidebar
        activeSessionId={chat.sessionId}
        onSelect={onSelect}
        onNew={onNew}
        onExamplePrompt={(p) => setPrefill(p)}
        appVersion={healthQ.data?.agent_version ?? "v0.3.2"}
        region={healthQ.data?.region ?? "EMEA"}
      />
      <main className="flex h-full min-w-0 flex-1 flex-col">
        <Header
          user={DEMO_USER}
          claimId={activeClaimId}
          health={healthQ.data?.status ?? "ok"}
        />
        <ChatPane
          messages={chat.messages}
          sessionId={chat.sessionId}
        />
        <ChatInput
          key={inputKey}
          onSend={(t) => {
            setPrefill(undefined);
            chat.send(t);
          }}
          onAbort={chat.abort}
          streaming={chat.streaming}
          language={language}
          onLanguageChange={setLanguage}
          prefilled={prefill}
        />
      </main>
    </div>
  );
}

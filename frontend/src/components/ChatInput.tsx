import { useRef, useState } from "react";
import { ArrowUp, Globe, Square } from "lucide-react";
import { cn } from "@/lib/utils";
import type { Language } from "@/types";

interface Props {
  onSend: (text: string) => void;
  onAbort: () => void;
  streaming: boolean;
  language: Language;
  onLanguageChange: (l: Language) => void;
  prefilled?: string;
}

const LANGUAGES: { value: Language; label: string }[] = [
  { value: "en", label: "EN" },
  { value: "es", label: "ES" },
  { value: "ja", label: "JA" },
];

export function ChatInput({
  onSend, onAbort, streaming, language, onLanguageChange, prefilled,
}: Props) {
  const [text, setText] = useState(prefilled ?? "");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Auto-grow
  const onChange = (v: string) => {
    setText(v);
    requestAnimationFrame(() => {
      const ta = textareaRef.current;
      if (ta) {
        ta.style.height = "auto";
        ta.style.height = Math.min(ta.scrollHeight, 240) + "px";
      }
    });
  };

  const submit = () => {
    const v = text.trim();
    if (!v) return;
    onSend(v);
    setText("");
    if (textareaRef.current) textareaRef.current.style.height = "auto";
  };

  return (
    <div className="border-t border-line bg-surface px-6 py-3">
      <div className="mx-auto flex max-w-3xl flex-col gap-1.5">
        <div
          className={cn(
            "flex items-end gap-2 rounded-xl border border-line bg-surface px-3 py-2 shadow-card",
            "focus-within:shadow-focus focus-within:border-brand/40",
          )}
        >
          <div className="flex flex-col items-stretch">
            <div className="flex items-center gap-1 rounded-md bg-sunken px-1 py-0.5 text-[11px] text-muted">
              <Globe className="h-3 w-3" />
              <select
                value={language}
                onChange={(e) => onLanguageChange(e.target.value as Language)}
                className="appearance-none bg-transparent font-mono text-[11px] focus:outline-none"
              >
                {LANGUAGES.map((l) => (
                  <option key={l.value} value={l.value}>{l.label}</option>
                ))}
              </select>
            </div>
          </div>
          <textarea
            ref={textareaRef}
            value={text}
            onChange={(e) => onChange(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                submit();
              }
            }}
            placeholder="Ask about coverage, repair cost, precedents, or draft a customer message…"
            rows={1}
            className="flex-1 resize-none bg-transparent text-[14px] leading-relaxed outline-none placeholder:text-soft"
          />
          {!streaming ? (
            <button
              onClick={submit}
              disabled={!text.trim()}
              className={cn(
                "grid h-8 w-8 place-items-center rounded-lg transition-colors",
                text.trim()
                  ? "bg-brand text-white hover:bg-brand2"
                  : "bg-sunken text-soft cursor-not-allowed",
              )}
              title="Send (Enter)"
            >
              <ArrowUp className="h-4 w-4" />
            </button>
          ) : (
            <button
              onClick={onAbort}
              className="grid h-8 w-8 place-items-center rounded-lg bg-ink text-white hover:bg-ink/90"
              title="Stop"
            >
              <Square className="h-3.5 w-3.5" />
            </button>
          )}
        </div>
        <div className="px-1 text-[11px] text-soft">
          ClaimsCopilot drafts recommendations. The adjuster makes every decision.
          Enter to send · Shift+Enter for newline.
        </div>
      </div>
    </div>
  );
}

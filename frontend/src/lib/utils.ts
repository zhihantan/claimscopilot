import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]): string {
  return twMerge(clsx(inputs));
}

export function formatCost(usd: number | undefined): string {
  if (usd === undefined) return "—";
  if (usd < 0.01) return `$${(usd * 1000).toFixed(2)}m`; // millis
  return `$${usd.toFixed(3)}`;
}

export function formatLatency(ms: number | undefined): string {
  if (ms === undefined) return "—";
  if (ms < 1000) return `${ms}ms`;
  return `${(ms / 1000).toFixed(1)}s`;
}

export function decisionTone(d?: string): {
  bg: string; text: string; ring: string; label: string;
} {
  switch (d) {
    case "APPROVE":
      return { bg: "bg-ok/10", text: "text-ok", ring: "ring-ok/20", label: "Approve" };
    case "PARTIAL_APPROVE":
      return { bg: "bg-info/10", text: "text-info", ring: "ring-info/20", label: "Partial" };
    case "DENY":
      return { bg: "bg-danger/10", text: "text-danger", ring: "ring-danger/20", label: "Deny" };
    case "REQUEST_DOCS":
      return { bg: "bg-warn/10", text: "text-warn", ring: "ring-warn/20", label: "Request docs" };
    case "UPDATE_STATUS":
      return { bg: "bg-muted/10", text: "text-ink", ring: "ring-line", label: "Update" };
    default:
      return { bg: "bg-muted/5", text: "text-muted", ring: "ring-line", label: "Undetermined" };
  }
}

export async function copyToClipboard(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    return false;
  }
}

export function relativeTime(iso: string): string {
  const d = new Date(iso);
  const s = Math.floor((Date.now() - d.getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return d.toLocaleDateString();
}

export function uid(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto)
    return crypto.randomUUID();
  return Math.random().toString(36).slice(2);
}

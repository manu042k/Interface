"use client";

import { useEffect, useRef, useState } from "react";
import {
  Brain,
  ShieldCheck,
  ShieldAlert,
  MousePointerClick,
  CheckCircle2,
  RefreshCw,
  Flag,
  AlertTriangle,
  Crosshair,
  Radio,
  Container,
  Camera,
  Coins,
  Gauge,
  Ban,
  Hand,
} from "lucide-react";
import { wsUrl } from "@/lib/api";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";

type Ev = Record<string, unknown> & { event: string; step?: number | null };

type Style = { Icon: React.ElementType; chip: string; text: string };

const NEUTRAL: Style = {
  Icon: Radio,
  chip: "bg-muted text-muted-foreground",
  text: "text-muted-foreground",
};
const BLUE: Style = {
  Icon: Brain,
  chip: "bg-sky-500/15 text-sky-700 dark:text-sky-300",
  text: "text-sky-700 dark:text-sky-300",
};
const GREEN: Style = {
  Icon: CheckCircle2,
  chip: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300",
  text: "text-emerald-700 dark:text-emerald-300",
};
const AMBER: Style = {
  Icon: AlertTriangle,
  chip: "bg-amber-500/15 text-amber-700 dark:text-amber-300",
  text: "text-amber-700 dark:text-amber-300",
};
const ROSE: Style = {
  Icon: AlertTriangle,
  chip: "bg-rose-500/15 text-rose-700 dark:text-rose-300",
  text: "text-rose-700 dark:text-rose-300",
};

const BASE: Record<string, Style> = {
  run_started: { ...NEUTRAL, Icon: Radio },
  sandbox_started: { ...NEUTRAL, Icon: Container },
  sandbox_stopped: { ...NEUTRAL, Icon: Container },
  sandbox_held: { ...AMBER, Icon: Container },
  session_held: { ...AMBER, Icon: Hand },
  evidence: { ...NEUTRAL, Icon: Camera },
  tokens: { ...NEUTRAL, Icon: Coins },
  decision: { ...BLUE, Icon: Brain },
  llm_call: { ...BLUE, Icon: Brain },
  locator_resolution: { ...BLUE, Icon: Crosshair },
  action: { ...GREEN, Icon: MousePointerClick },
  checkpoint: { ...GREEN, Icon: CheckCircle2 },
  run_finished: { ...GREEN, Icon: CheckCircle2 },
  intervention_resolved: { ...GREEN, Icon: CheckCircle2 },
  control_transferred: { ...GREEN, Icon: Hand },
  human_action: { ...AMBER, Icon: Hand },
  intervention_opened: { ...AMBER, Icon: Flag },
  recoverable_condition: { ...AMBER, Icon: RefreshCw },
  business_outcome: { ...AMBER, Icon: Flag },
  provider_throttle: { ...AMBER, Icon: Gauge },
  provider_retry: { ...AMBER, Icon: RefreshCw },
  provider_rotate: { ...AMBER, Icon: RefreshCw },
  provider_degraded: { ...AMBER, Icon: AlertTriangle },
  all_providers_exhausted: { ...AMBER, Icon: AlertTriangle },
  provider_disabled: { ...ROSE, Icon: Ban },
  stuck: { ...ROSE, Icon: AlertTriangle },
  dead_end: { ...ROSE, Icon: AlertTriangle },
  hard_failure: { ...ROSE, Icon: AlertTriangle },
  run_stopped: { ...ROSE, Icon: Ban },
};

function styleFor(e: Ev): Style {
  if (e.event === "guardrail") {
    return e.verdict === "block" || e.verdict === "require_confirmation"
      ? { ...ROSE, Icon: ShieldAlert }
      : { ...GREEN, Icon: ShieldCheck };
  }
  return BASE[e.event] ?? NEUTRAL;
}

export function EventTimeline({ runId }: { runId: string }) {
  const [events, setEvents] = useState<Ev[]>([]);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const ws = new WebSocket(wsUrl(`/ws/runs/${runId}/events`));
    ws.onmessage = (m) => {
      try {
        const ev = JSON.parse(m.data) as Ev;
        if (ev.event === "ping") return;
        setEvents((prev) => [...prev, ev]);
      } catch {
        /* ignore */
      }
    };
    return () => ws.close();
  }, [runId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [events.length]);

  return (
    <div className="bg-card flex h-full min-h-0 flex-col overflow-hidden rounded-lg border">
      <div className="border-border/60 flex items-center justify-between border-b px-3 py-2 text-xs font-medium uppercase tracking-wide">
        <span>Event timeline</span>
        <span className="text-muted-foreground normal-case">
          {events.length} events
        </span>
      </div>
      <ScrollArea className="min-h-0 flex-1">
        <ol className="p-2 font-mono text-xs">
          {events.map((e, i) => {
            const s = styleFor(e);
            const detail =
              (typeof e.reasoning === "string" && e.reasoning) ||
              (typeof e.reason === "string" && e.reason) ||
              (typeof e.description === "string" && e.description) ||
              "";
            const meta = [
              typeof e.tool === "string" ? e.tool : null,
              typeof e.verdict === "string" ? e.verdict : null,
              typeof e.provider === "string" ? e.provider : null,
              typeof e.code === "string" ? e.code : null,
              typeof e.matched_strategy === "string" ? e.matched_strategy : null,
              e.event === "tokens"
                ? `${e.tokens_in ?? 0} in / ${e.tokens_out ?? 0} out`
                : null,
            ].filter(Boolean);
            return (
              <li
                key={i}
                className="hover:bg-muted/40 flex items-start gap-2 rounded px-1.5 py-1 transition-colors"
              >
                <s.Icon className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", s.text)} />
                <span className="text-muted-foreground/60 w-10 shrink-0 pt-0.5 tabular-nums">
                  {e.step != null ? `s${e.step}` : ""}
                </span>
                <div className="min-w-0 flex-1 leading-relaxed">
                  <span
                    className={cn(
                      "rounded px-1 py-0.5 text-[10px] font-bold uppercase tracking-wider",
                      s.chip,
                    )}
                  >
                    {e.event.replaceAll("_", " ")}
                  </span>
                  {meta.map((m, j) => (
                    <span
                      key={j}
                      className="text-muted-foreground ml-1.5 text-[11px]"
                    >
                      {m}
                    </span>
                  ))}
                  {detail && (
                    <p className={cn("mt-0.5 not-italic", s.text, "opacity-80")}>
                      {detail}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
          {events.length === 0 && (
            <li className="text-muted-foreground p-2">waiting for events…</li>
          )}
          <div ref={endRef} />
        </ol>
      </ScrollArea>
    </div>
  );
}

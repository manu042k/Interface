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

type Ev = Record<string, unknown> & {
  event: string;
  step?: number | null;
  ts?: number;
};

type Style = { Icon: React.ElementType; color: string };

const NEUTRAL: Style = { Icon: Radio, color: "text-zinc-400" };
const BLUE: Style = { Icon: Brain, color: "text-sky-400" };
const GREEN: Style = { Icon: CheckCircle2, color: "text-emerald-400" };
const AMBER: Style = { Icon: AlertTriangle, color: "text-amber-400" };
const ROSE: Style = { Icon: AlertTriangle, color: "text-rose-400" };

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

function clock(ts?: number): string {
  const d = ts ? new Date(ts * 1000) : new Date();
  return d.toLocaleTimeString([], {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

export function EventTimeline({ runId }: { runId: string }) {
  const [events, setEvents] = useState<Ev[]>([]);
  const [live, setLive] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const ws = new WebSocket(wsUrl(`/ws/runs/${runId}/events`));
    ws.onopen = () => setLive(true);
    ws.onclose = () => setLive(false);
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
    <div className="flex h-full min-h-0 flex-col overflow-hidden rounded-lg border border-zinc-800 bg-[#0c0c0e] text-zinc-300 shadow-inner">
      {/* title bar */}
      <div className="flex shrink-0 items-center gap-2 border-b border-zinc-800 bg-[#161619] px-3 py-2">
        <span className="flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-full bg-rose-500/70" />
          <span className="h-2.5 w-2.5 rounded-full bg-amber-500/70" />
          <span className="h-2.5 w-2.5 rounded-full bg-emerald-500/70" />
        </span>
        <span className="font-heading flex-1 text-center text-xs tracking-wide text-zinc-500">
          Agent · event log
        </span>
        <span className="font-mono text-[10px] text-zinc-600">
          {events.length}
        </span>
      </div>

      <ScrollArea className="min-h-0 flex-1">
        <div className="p-2.5 font-mono text-[11px] leading-relaxed">
          {events.map((e, i) => {
            const s = styleFor(e);
            const detail =
              (typeof e.reasoning === "string" && e.reasoning) ||
              (typeof e.reason === "string" && e.reason) ||
              (typeof e.description === "string" && e.description) ||
              (typeof e.detail === "string" && e.detail) ||
              "";
            const urlAfter =
              typeof e.url_after === "string" && e.url_after
                ? (() => {
                    try {
                      const u = new URL(e.url_after as string);
                      return `→ ${u.pathname}${u.search}`;
                    } catch {
                      return `→ ${e.url_after}`;
                    }
                  })()
                : null;
            const meta = [
              typeof e.tool === "string" ? e.tool : null,
              typeof e.verdict === "string" ? e.verdict : null,
              typeof e.provider === "string" ? e.provider : null,
              typeof e.code === "string" ? e.code : null,
              typeof e.matched_strategy === "string" ? e.matched_strategy : null,
              e.drift_signal === true ? "drift" : null,
              e.timed_out === true ? "timed out" : null,
              e.ok === false ? "✗" : null,
              urlAfter,
              e.event === "tokens"
                ? `${e.tokens_in ?? 0}in/${e.tokens_out ?? 0}out`
                : null,
            ].filter(Boolean);
            return (
              <div key={i} className="px-1 py-[3px]">
                <div className="flex items-start gap-2">
                  <span className="shrink-0 tabular-nums text-zinc-600">
                    {clock(e.ts)}
                  </span>
                  <s.Icon
                    className={cn("mt-[3px] h-3 w-3 shrink-0", s.color)}
                  />
                  <span className="shrink-0 text-zinc-600">
                    {e.step != null ? `s${e.step}` : "  ·"}
                  </span>
                  <span className={cn("font-semibold uppercase", s.color)}>
                    {e.event.replaceAll("_", " ")}
                  </span>
                  {meta.map((m, j) => (
                    <span key={j} className="text-zinc-500">
                      {m}
                    </span>
                  ))}
                </div>
                {detail && (
                  <div className="pl-[3.6rem] text-zinc-400/80">
                    <span className="text-zinc-600">↳ </span>
                    {detail}
                  </div>
                )}
              </div>
            );
          })}
          {events.length === 0 && (
            <div className="px-1 py-1 text-zinc-600">
              <span className="text-emerald-500">$</span> waiting for events…
            </div>
          )}
          {live && (
            <div className="flex items-center gap-1 px-1 pt-1 text-zinc-600">
              <span className="text-emerald-500">$</span>
              <span className="inline-block h-3 w-1.5 animate-pulse bg-zinc-500" />
            </div>
          )}
          <div ref={endRef} />
        </div>
      </ScrollArea>
    </div>
  );
}

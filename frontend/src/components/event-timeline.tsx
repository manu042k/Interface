"use client";

import { useEffect, useRef, useState } from "react";
import {
  Brain,
  ShieldCheck,
  MousePointerClick,
  CheckCircle2,
  RefreshCw,
  Flag,
  AlertTriangle,
  Crosshair,
  Radio,
  Container,
} from "lucide-react";
import { wsUrl } from "@/lib/api";
import { ScrollArea } from "@/components/ui/scroll-area";
import { cn } from "@/lib/utils";

type Ev = Record<string, unknown> & { event: string; step?: number | null };

const ICON: Record<string, React.ElementType> = {
  run_started: Radio,
  sandbox_started: Container,
  decision: Brain,
  guardrail: ShieldCheck,
  action: MousePointerClick,
  checkpoint: CheckCircle2,
  locator_resolution: Crosshair,
  recoverable_condition: RefreshCw,
  business_outcome: Flag,
  stuck: AlertTriangle,
  hard_failure: AlertTriangle,
  intervention_opened: Flag,
  control_transferred: Flag,
  human_action: MousePointerClick,
  intervention_resolved: CheckCircle2,
  run_finished: CheckCircle2,
};

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
    <div className="bg-card flex h-full min-h-[420px] flex-col rounded-lg border">
      <div className="border-border/60 border-b px-3 py-2 text-xs font-medium uppercase tracking-wide">
        Event timeline
      </div>
      <ScrollArea className="flex-1">
        <ol className="space-y-0.5 p-3">
          {events.map((e, i) => {
            const Icon = ICON[e.event] ?? Radio;
            const tone =
              e.event === "hard_failure" || e.event === "stuck"
                ? "text-destructive"
                : e.event === "business_outcome"
                  ? "text-warning"
                  : e.event === "run_finished" || e.event === "checkpoint"
                    ? "text-success"
                    : "text-muted-foreground";
            return (
              <li key={i} className="flex gap-2.5 py-1 text-sm">
                <Icon className={cn("mt-0.5 h-3.5 w-3.5 shrink-0", tone)} />
                <div className="min-w-0">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    {e.step != null && (
                      <span className="text-muted-foreground text-[11px]">
                        step {e.step}
                      </span>
                    )}
                    <span className="font-medium">
                      {e.event.replaceAll("_", " ")}
                    </span>
                    {typeof e.tool === "string" && (
                      <code className="text-[11px]">{e.tool}</code>
                    )}
                    {typeof e.verdict === "string" && (
                      <code className="text-[11px]">{e.verdict}</code>
                    )}
                    {typeof e.code === "string" && (
                      <code className="text-warning text-[11px]">{e.code}</code>
                    )}
                    {typeof e.matched_strategy === "string" && (
                      <code className="text-[11px]">{e.matched_strategy}</code>
                    )}
                  </div>
                  {typeof e.reasoning === "string" && e.reasoning && (
                    <p className="text-muted-foreground text-xs italic">
                      {e.reasoning}
                    </p>
                  )}
                  {typeof e.reason === "string" && e.reason && (
                    <p className="text-muted-foreground text-xs">{e.reason}</p>
                  )}
                  {typeof e.description === "string" && e.description && (
                    <p className="text-muted-foreground text-xs">
                      {e.description}
                    </p>
                  )}
                </div>
              </li>
            );
          })}
          {events.length === 0 && (
            <li className="text-muted-foreground p-2 text-sm">
              waiting for events…
            </li>
          )}
          <div ref={endRef} />
        </ol>
      </ScrollArea>
    </div>
  );
}

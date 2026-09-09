"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  FileText,
  TerminalSquare,
  CheckCircle2,
  XCircle,
  ChevronUp,
  Cpu,
  Coins,
  Clock,
  Globe,
  Footprints,
} from "lucide-react";
import { api, type RunView } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/badges";
import { NoVncFrame } from "@/components/novnc-frame";
import { EventTimeline } from "@/components/event-timeline";
import { SandboxTerminal } from "@/components/sandbox-terminal";
import { HandoffPanel } from "@/components/handoff-panel";

const TERMINAL = new Set(["completed", "failed", "dead_end"]);

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function elapsed(run: RunView): string {
  if (!run.started_at) return "—";
  const end = run.ended_at ?? Date.now() / 1000;
  const s = Math.max(0, Math.round(end - run.started_at));
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

function Stat({
  icon: Icon,
  children,
}: {
  icon: React.ElementType;
  children: React.ReactNode;
}) {
  return (
    <span className="text-muted-foreground flex items-center gap-1.5 text-xs">
      <Icon className="h-3.5 w-3.5 shrink-0 opacity-60" />
      {children}
    </span>
  );
}

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const [showTerm, setShowTerm] = useState(false);
  const [handled, setHandled] = useState(false);

  const { data: run, refetch } = useQuery({
    queryKey: ["run", id],
    queryFn: () => api.run(id),
    refetchInterval: (q) =>
      q.state.data && TERMINAL.has(q.state.data.status) ? false : 1500,
  });

  const isStuck = run?.status === "stuck" && !handled;

  // The live canvas stays view-only for the whole run. Input unlocks only once
  // the model has escalated (run → stuck) AND an operator has claimed the
  // handoff — never while automation is driving.
  const { data: intervention } = useQuery({
    queryKey: ["run-intervention", id],
    queryFn: () => api.runIntervention(id),
    enabled: isStuck,
    refetchInterval: 2000,
  });
  const inControl = isStuck && intervention?.status === "claimed";
  const ended =
    !!run && (TERMINAL.has(run.status) || (run.status === "stuck" && handled));
  const ok = run?.status === "completed";
  const canTerm = !!run?.sandbox_container && !ended;
  const tokens = (run?.tokens_in ?? 0) + (run?.tokens_out ?? 0);

  return (
    <div className="flex h-full min-h-[640px] flex-col gap-3">
      {/* Title */}
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="truncate text-2xl font-semibold tracking-tight">
            {run?.name || run?.goal || "…"}
          </h1>
          {run?.name && run?.goal && (
            <p className="text-muted-foreground mt-0.5 line-clamp-2 text-sm">
              {run.goal}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <StatusBadge status={run?.status} />
          {run?.artifact_id && (
            <Button asChild size="sm">
              <Link href={`/runs/${id}/report`}>
                <FileText className="mr-1.5 h-4 w-4" /> Report
              </Link>
            </Button>
          )}
        </div>
      </header>

      {/* Stats bar */}
      {run && (
        <div className="bg-card flex flex-wrap items-center gap-x-4 gap-y-1.5 rounded-lg border px-3 py-2">
          <code className="text-muted-foreground text-[11px]">
            {id.slice(0, 12)}
          </code>
          <span className="bg-border h-3.5 w-px" />
          <Stat icon={Footprints}>
            <span className="text-foreground font-mono font-medium">
              {run.step_count}
            </span>{" "}
            steps
          </Stat>
          <Stat icon={Coins}>
            <span className="text-foreground font-mono font-medium">
              {tokens.toLocaleString()}
            </span>{" "}
            tokens
            {run.llm_calls > 0 && (
              <span className="opacity-60"> · {run.llm_calls} calls</span>
            )}
          </Stat>
          <Stat icon={Clock}>
            <span className="text-foreground font-mono font-medium">
              {elapsed(run)}
            </span>
          </Stat>
          <span className="bg-border h-3.5 w-px" />
          <Stat icon={Cpu}>{run.browser}</Stat>
          <Stat icon={Globe}>
            <span className="font-mono">{hostOf(run.app_target)}</span>
          </Stat>
        </div>
      )}

      {isStuck && (
        <HandoffPanel
          runId={id}
          onResolved={() => {
            setHandled(true);
            refetch();
          }}
        />
      )}

      {ended && (
        <div
          className={`flex flex-wrap items-center gap-2 rounded-lg border p-3 text-sm ${
            ok || run?.status === "stuck"
              ? "border-success/40 bg-success/8"
              : "border-destructive/40 bg-destructive/8"
          }`}
        >
          {ok || run?.status === "stuck" ? (
            <CheckCircle2 className="text-success h-4 w-4" />
          ) : (
            <XCircle className="text-destructive h-4 w-4" />
          )}
          <span className="font-medium">
            {run?.status === "stuck" && handled
              ? "Operator handled this run; control returned to automation."
              : `Run ${run?.status}`}
          </span>
          {run?.detail && (
            <span className="text-muted-foreground">— {run.detail}</span>
          )}
          {run?.artifact_id && (
            <>
              <span className="text-muted-foreground">·</span>
              <Link
                href="/capabilities"
                className="text-primary underline underline-offset-2"
              >
                {run.record_outcome === "reused" ? "matched" : "recorded"}{" "}
                {run.artifact_id.slice(0, 8)} v{run.artifact_version}
              </Link>
              <span className="text-muted-foreground">
                {run.record_outcome === "reused"
                  ? "(reproduced an existing capability — confirmation logged, no new draft)"
                  : run.record_outcome === "updated_draft"
                    ? "(flow changed — updated the pending draft, review to approve)"
                    : run.record_outcome === "new_version"
                      ? "(flow differs from the approved version — saved as a new draft, review to approve)"
                      : "(new draft — review to approve)"}
              </span>
            </>
          )}
        </div>
      )}

      {/* Live view + timeline */}
      <div className="grid min-h-0 flex-1 gap-3 lg:grid-cols-[1.4fr_1fr]">
        <NoVncFrame
          novncUrl={run?.novnc_url ?? null}
          interactive={!!inControl}
          ended={ended && !isStuck}
        />
        <EventTimeline runId={id} />
      </div>

      {/* Terminal drawer */}
      {canTerm && (
        <div className="shrink-0">
          {showTerm && <SandboxTerminal runId={id} />}
          <button
            type="button"
            onClick={() => setShowTerm((v) => !v)}
            className="bg-card text-muted-foreground hover:text-foreground mt-1.5 flex w-full items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors"
          >
            <TerminalSquare className="h-3.5 w-3.5" />
            Sandbox terminal
            <ChevronUp
              className={`ml-auto h-3.5 w-3.5 transition-transform ${showTerm ? "" : "rotate-180"}`}
            />
          </button>
        </div>
      )}
    </div>
  );
}

"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { FileText, TerminalSquare, CheckCircle2, XCircle } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/badges";
import { NoVncFrame } from "@/components/novnc-frame";
import { EventTimeline } from "@/components/event-timeline";
import { SandboxTerminal } from "@/components/sandbox-terminal";
import { HandoffPanel } from "@/components/handoff-panel";

const TERMINAL = new Set(["completed", "failed", "dead_end"]);

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

  // The live canvas stays view-only for the whole run. Input is only unlocked
  // once the model has escalated (run → stuck) AND an operator has claimed the
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

  return (
    <div className="flex h-full min-h-[640px] flex-col gap-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <h1 className="truncate text-xl font-semibold tracking-tight">
            {run?.goal ?? "…"}
          </h1>
          <code className="text-muted-foreground text-xs">{id.slice(0, 12)}</code>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge status={run?.status} />
          {run?.sandbox_container && !ended && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => setShowTerm((v) => !v)}
            >
              <TerminalSquare className="mr-1.5 h-4 w-4" />
              {showTerm ? "Hide terminal" : "Terminal"}
            </Button>
          )}
          {run?.artifact_id && (
            <Button asChild size="sm">
              <Link href={`/runs/${id}/report`}>
                <FileText className="mr-1.5 h-4 w-4" /> Report
              </Link>
            </Button>
          )}
        </div>
      </header>

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
            ok
              ? "border-success/40 bg-success/8"
              : run?.status === "stuck"
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

      <div className="grid min-h-0 flex-1 gap-4 lg:grid-cols-[1.35fr_1fr]">
        <NoVncFrame
          novncUrl={run?.novnc_url ?? null}
          interactive={!!inControl}
          ended={ended && !isStuck}
        />
        <EventTimeline runId={id} />
      </div>

      {showTerm && run?.sandbox_container && !ended && (
        <SandboxTerminal runId={id} />
      )}
    </div>
  );
}

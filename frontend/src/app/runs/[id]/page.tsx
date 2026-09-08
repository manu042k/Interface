"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  FileText,
  TerminalSquare,
  ChevronRight,
  CheckCircle2,
  XCircle,
} from "lucide-react";
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
  const inControl = isStuck;
  const ended =
    !!run && (TERMINAL.has(run.status) || (run.status === "stuck" && handled));
  const ok = run?.status === "completed";

  return (
    <div className="flex h-full min-h-[640px] flex-col gap-4">
      <header className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="text-muted-foreground flex items-center gap-1.5 text-xs">
            <Link href="/" className="hover:text-foreground">
              runs
            </Link>
            <ChevronRight className="h-3 w-3" />
            <code>{id.slice(0, 12)}</code>
          </div>
          <h1 className="truncate text-xl font-semibold tracking-tight">
            {run?.goal ?? "…"}
          </h1>
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
                href="/review"
                className="text-primary underline underline-offset-2"
              >
                artifact {run.artifact_id.slice(0, 8)} v{run.artifact_version}
              </Link>
              <span className="text-muted-foreground">
                (draft — review to approve)
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

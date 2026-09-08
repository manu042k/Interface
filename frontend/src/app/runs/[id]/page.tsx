"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { FileText, TerminalSquare, ChevronRight } from "lucide-react";
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

  const { data: run, refetch } = useQuery({
    queryKey: ["run", id],
    queryFn: () => api.run(id),
    refetchInterval: (q) =>
      q.state.data && TERMINAL.has(q.state.data.status) ? false : 1500,
  });

  const inControl = run?.status === "stuck";
  const done = run && TERMINAL.has(run.status);

  return (
    <div className="space-y-4">
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
          {run?.sandbox_container && (
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

      {run?.status === "stuck" && (
        <HandoffPanel runId={id} onResolved={() => refetch()} />
      )}

      <div className="grid gap-4 lg:grid-cols-[1.35fr_1fr]">
        <NoVncFrame novncUrl={run?.novnc_url ?? null} interactive={!!inControl} />
        <EventTimeline runId={id} />
      </div>

      {showTerm && run?.sandbox_container && <SandboxTerminal runId={id} />}

      {done && (
        <p className="text-muted-foreground text-sm">
          Run {run.status}
          {run.detail ? ` — ${run.detail}` : ""}.{" "}
          {run.artifact_id ? (
            <>
              Recorded artifact{" "}
              <Link
                href={`/review`}
                className="text-primary underline underline-offset-2"
              >
                {run.artifact_id.slice(0, 8)} v{run.artifact_version}
              </Link>{" "}
              (draft — review to approve).
            </>
          ) : null}
        </p>
      )}
    </div>
  );
}

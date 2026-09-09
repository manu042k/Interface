"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import {
  FileText,
  CheckCircle2,
  XCircle,
  Ban,
  Loader2,
  Cpu,
  Coins,
  Clock,
  Globe,
  Footprints,
  ChevronDown,
} from "lucide-react";
import { api, type RunView } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { StatusBadge } from "@/components/badges";
import { NoVncFrame, LiveFeed } from "@/components/novnc-frame";
import { EventTimeline } from "@/components/event-timeline";
import { HandoffPanel } from "@/components/handoff-panel";
import { RunReport } from "@/components/run-report";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";

const TERMINAL = new Set(["completed", "failed", "dead_end"]);

function hostOf(url: string): string {
  try {
    return new URL(url).host;
  } catch {
    return url;
  }
}

function elapsed(run: RunView): string {
  if (!run.started_at) return "-";
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

function ago(ts: number | null): string {
  if (!ts) return "-";
  return new Date(ts * 1000).toLocaleString();
}

function DetailRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex gap-3 py-1.5">
      <span className="text-muted-foreground w-32 shrink-0 text-xs">
        {label}
      </span>
      <span className="min-w-0 flex-1 break-words text-xs">{children}</span>
    </div>
  );
}

export default function RunPage() {
  const { id } = useParams<{ id: string }>();
  const [showDetails, setShowDetails] = useState(false);
  const [expanded, setExpanded] = useState(false);

  const { data: run, refetch } = useQuery({
    queryKey: ["run", id],
    queryFn: () => api.run(id),
    refetchInterval: (q) =>
      q.state.data && TERMINAL.has(q.state.data.status) ? false : 1500,
  });

  // A stuck run now resumes automation after hand-back, so `stuck` is a
  // transient state, not an end state.
  const isStuck = run?.status === "stuck";

  // The live canvas stays view-only for the whole run. Input unlocks only once
  // the model has escalated (run → stuck) AND an operator has claimed the
  // handoff - never while automation is driving.
  const { data: intervention } = useQuery({
    queryKey: ["run-intervention", id],
    queryFn: () => api.runIntervention(id),
    enabled: isStuck,
    refetchInterval: 2000,
  });
  const inControl = isStuck && intervention?.status === "claimed";
  const ended = !!run && TERMINAL.has(run.status);
  const ok = run?.status === "completed";
  const tokens = (run?.tokens_in ?? 0) + (run?.tokens_out ?? 0);
  const isReplay = run?.mode === "replay";

  const cancel = useMutation({
    mutationFn: () => api.cancelRun(id),
    onSuccess: () => {
      toast.success("Run cancelled");
      refetch();
    },
    onError: (e) => toast.error(String((e as Error).message)),
  });

  return (
    <div className="flex h-full min-h-[640px] flex-col gap-3">
      {/* Title */}
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="truncate text-2xl font-semibold tracking-tight">
            {run?.name || run?.goal || "…"}
          </h1>
          {run?.name && run?.goal && run.name !== run.goal && (
            <p className="text-muted-foreground mt-0.5 line-clamp-2 text-sm">
              {run.goal}
            </p>
          )}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <StatusBadge status={run?.status} />
          {run && !ended && (
            <Button
              size="sm"
              variant="outline"
              className="text-destructive hover:text-destructive border-destructive/30"
              onClick={() => cancel.mutate()}
              disabled={cancel.isPending}
            >
              {cancel.isPending ? (
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
              ) : (
                <Ban className="mr-1.5 h-4 w-4" />
              )}
              Cancel run
            </Button>
          )}
          {ended && (
            <Button asChild size="sm" variant="outline">
              <Link href={`/runs/${id}/report`}>
                <FileText className="mr-1.5 h-4 w-4" /> Full report / print
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
          <button
            type="button"
            onClick={() => setShowDetails((v) => !v)}
            className="text-muted-foreground hover:text-foreground ml-auto flex items-center gap-1 text-xs font-medium"
          >
            Details
            <ChevronDown
              className={`h-3.5 w-3.5 transition-transform ${showDetails ? "rotate-180" : ""}`}
            />
          </button>
        </div>
      )}

      {run && showDetails && (
        <div className="bg-card shrink-0 divide-y divide-border/50 rounded-lg border px-3 py-1">
          <DetailRow label="Goal name">{run.name || "-"}</DetailRow>
          <DetailRow label="Description">
            {run.goal || "-"}
          </DetailRow>
          <DetailRow label="Run ID">
            <code>{id}</code>
          </DetailRow>
          <DetailRow label="Target">
            <a
              href={run.app_target}
              target="_blank"
              rel="noreferrer"
              className="text-primary break-all underline underline-offset-2"
            >
              {run.app_target}
            </a>
          </DetailRow>
          <DetailRow label="Tenant">{run.tenant_id}</DetailRow>
          <DetailRow label="Browser">{run.browser}</DetailRow>
          <DetailRow label="Parameters">
            {Object.keys(run.params ?? {}).length === 0 ? (
              "-"
            ) : (
              <div className="flex flex-wrap gap-1.5">
                {Object.entries(run.params).map(([k, v]) => (
                  <code
                    key={k}
                    className="bg-muted rounded px-1.5 py-0.5 text-[11px]"
                  >
                    {k}={String(v)}
                  </code>
                ))}
              </div>
            )}
          </DetailRow>
          <DetailRow label="Steps">{run.step_count}</DetailRow>
          <DetailRow label="LLM">
            {run.llm_calls} calls · {run.tokens_in.toLocaleString()} in /{" "}
            {run.tokens_out.toLocaleString()} out ({tokens.toLocaleString()}{" "}
            total)
          </DetailRow>
          <DetailRow label="Started">{ago(run.started_at)}</DetailRow>
          <DetailRow label="Ended">
            {run.ended_at ? `${ago(run.ended_at)} · ${elapsed(run)}` : "running"}
          </DetailRow>
          <DetailRow label="Status">
            {run.status}
            {run.detail ? ` - ${run.detail}` : ""}
          </DetailRow>
          {run.artifact_id && (
            <DetailRow label="Artifact">
              <Link
                href="/capabilities"
                className="text-primary underline underline-offset-2"
              >
                {run.artifact_id.slice(0, 8)} v{run.artifact_version}
              </Link>
              {run.record_outcome ? ` · ${run.record_outcome}` : ""}
            </DetailRow>
          )}
        </div>
      )}

      {ended && (
        <div
          className={`flex flex-wrap items-center gap-2 rounded-lg border p-3 text-sm ${
            ok
              ? "border-success/40 bg-success/8"
              : "border-destructive/40 bg-destructive/8"
          }`}
        >
          {ok ? (
            <CheckCircle2 className="text-success h-4 w-4" />
          ) : (
            <XCircle className="text-destructive h-4 w-4" />
          )}
          <span className="font-medium">
            {isReplay ? "Replay" : "Run"} {run?.status}
          </span>
          {run?.detail && (
            <span className="text-muted-foreground">- {run.detail}</span>
          )}
          {run?.artifact_id && !isReplay && (
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
                  ? "(reproduced an existing capability - confirmation logged, no new draft)"
                  : run.record_outcome === "updated_draft"
                    ? "(flow changed - updated the pending draft, review to approve)"
                    : run.record_outcome === "new_version"
                      ? "(flow differs from the approved version - saved as a new draft, review to approve)"
                      : "(new draft - review to approve)"}
              </span>
            </>
          )}
          {run?.artifact_id && isReplay && (
            <>
              <span className="text-muted-foreground">·</span>
              <span className="text-muted-foreground">
                deterministic replay of{" "}
                <Link
                  href="/capabilities"
                  className="text-primary underline underline-offset-2"
                >
                  {run.artifact_id.slice(0, 8)} v{run.artifact_version}
                </Link>{" "}
                (no LLM in the loop)
              </span>
            </>
          )}
        </div>
      )}

      {ended ? (
        /* the live view is dead once the run finishes - show the report here */
        <div className="min-h-0 flex-1">
          <RunReport id={id} showSummary={false} />
        </div>
      ) : (
        <div className="grid gap-3 lg:grid-cols-[1.4fr_1fr]">
          {/* the live view stays put: self-start + the feed's own 16:9 aspect
              ratio fix its size, and the handoff prompt is OVERLAID on it (not
              inserted above) so nothing on the page ever shifts. */}
          <div className="relative min-w-0 self-start">
            <NoVncFrame
              novncUrl={run?.novnc_url ?? null}
              interactive={!!inControl}
              ended={false}
              onExpand={run?.novnc_url ? () => setExpanded(true) : undefined}
              starting={
                !run?.novnc_url &&
                (run?.status === "pending" || run?.status === "running")
              }
            />
            {isStuck && (
              <div className="absolute inset-x-0 top-9 z-10 p-2">
                <HandoffPanel runId={id} onResolved={() => refetch()} />
              </div>
            )}
          </div>
          <div className="h-[440px] min-w-0 self-start lg:h-[600px]">
            <EventTimeline runId={id} />
          </div>
        </div>
      )}

      {run?.novnc_url && (
        <Dialog open={expanded} onOpenChange={setExpanded}>
          <DialogContent className="w-auto max-w-[96vw] gap-0 overflow-hidden p-0 sm:max-w-[96vw]">
            <DialogTitle className="border-b px-4 py-2.5 text-sm">
              Live sandbox
              <span className="text-muted-foreground ml-2 text-xs font-normal">
                {inControl
                  ? "you are in control"
                  : "view only · automation driving"}
              </span>
            </DialogTitle>
            {/* cap by BOTH viewport width and height so the 16:9 feed always fits */}
            <div
              className="w-full"
              style={{ width: "min(92vw, calc(82vh * 1280 / 720))" }}
            >
              <LiveFeed novncUrl={run.novnc_url} interactive={!!inControl} />
            </div>
          </DialogContent>
        </Dialog>
      )}
    </div>
  );
}

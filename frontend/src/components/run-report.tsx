"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, API_BASE, type RunReport as RunReportData } from "@/lib/api";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { OutcomeBadge, StatusBadge } from "@/components/badges";
import { ArtifactView } from "@/components/artifact-view";

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex gap-2 py-0.5">
      <dt className="text-muted-foreground w-24 shrink-0">{k}</dt>
      <dd className="min-w-0 break-words">{v}</dd>
    </div>
  );
}

function pathOf(url: unknown): string {
  if (typeof url !== "string" || !url) return "";
  try {
    const u = new URL(url);
    return u.pathname + u.search;
  } catch {
    return url;
  }
}

/** step index encoded in an evidence filename: .../step12-screenshot-….png -> 12 */
function stepOfEvidence(path: string): number | null {
  const m = path.match(/\/step(\d+)-/);
  return m ? Number(m[1]) : null;
}

type StepBlock = {
  step: number | null;
  events: Record<string, unknown>[];
  shots: string[];
};

/** Interleave the timeline and the evidence: one block per step index, holding
 *  that step's events on the left and its screenshot(s) on the right. */
function buildBlocks(
  timeline: Record<string, unknown>[],
  evidence: string[],
): { blocks: StepBlock[]; orphanShots: string[]; docs: string[] } {
  const shotsByStep = new Map<number, string[]>();
  const orphanShots: string[] = [];
  const docs: string[] = [];
  for (const e of evidence) {
    if (!e.endsWith(".png")) {
      docs.push(e);
      continue;
    }
    const s = stepOfEvidence(e);
    if (s == null) orphanShots.push(e);
    else shotsByStep.set(s, [...(shotsByStep.get(s) ?? []), e]);
  }

  const blocks: StepBlock[] = [];
  for (const e of timeline) {
    const step = typeof e.step === "number" ? e.step : null;
    const last = blocks[blocks.length - 1];
    if (last && last.step === step) last.events.push(e);
    else blocks.push({ step, events: [e], shots: [] });
  }
  for (const b of blocks) {
    if (b.step != null) b.shots = shotsByStep.get(b.step) ?? [];
  }
  return { blocks, orphanShots, docs };
}

function Thumb({
  src,
  onOpen,
}: {
  src: string;
  onOpen: (src: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(src)}
      className="bg-muted/40 block w-full max-w-[280px] overflow-hidden rounded border transition hover:opacity-80 hover:ring-2 hover:ring-primary/40"
      title="Click to enlarge"
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={API_BASE + src}
        alt={src}
        className="h-40 w-full object-contain"
      />
    </button>
  );
}

/**
 * One timeline event, rendered so a fast (headless) replay can still be audited
 * after the fact: which locator strategy actually matched, whether it was a
 * fallback / drift, where each action left the page, and every checkpoint result.
 */
function TimelineRow({ e }: { e: Record<string, unknown> }) {
  const ev = String(e.event);
  const ok = e.ok as boolean | undefined;
  const bad =
    ok === false ||
    ev === "hard_failure" ||
    ev === "stuck" ||
    e.verdict === "block";

  const bits: React.ReactNode[] = [];
  if (typeof e.tool === "string") bits.push(e.tool);
  if (typeof e.action_type === "string") bits.push(e.action_type);
  if (typeof e.verdict === "string") bits.push(e.verdict);
  if (typeof e.matched_strategy === "string") {
    bits.push(
      <code key="loc" className="bg-muted rounded px-1 text-[11px]">
        {e.matched_strategy}
      </code>,
    );
  }
  if (e.drift_signal === true || (typeof e.matched_rank === "number" && e.matched_rank > 0)) {
    bits.push(
      <span key="drift" className="text-warning text-[11px] font-medium">
        fallback #{String(e.matched_rank ?? "?")} (drift)
      </span>,
    );
  }
  if (e.timed_out === true)
    bits.push(
      <span key="to" className="text-destructive text-[11px]">
        timed out
      </span>,
    );
  const after = pathOf(e.url_after);
  if (after)
    bits.push(
      <span key="url" className="text-muted-foreground text-[11px]">
        → {after}
      </span>,
    );
  if (typeof e.code === "string") bits.push(<code key="code">{e.code}</code>);
  if (typeof e.status === "string") bits.push(e.status);
  if (typeof e.rule === "string") bits.push(e.rule);
  if (typeof e.recovery === "string")
    bits.push(
      <span key="rec" className="text-muted-foreground text-[11px]">
        ({e.recovery})
      </span>,
    );

  const detail =
    (typeof e.description === "string" && e.description) ||
    (typeof e.reason === "string" && e.reason) ||
    (typeof e.error === "string" && e.error) ||
    (typeof e.reasoning === "string" && e.reasoning) ||
    "";

  return (
    <div className="flex flex-wrap items-baseline gap-x-2 py-0.5">
      <span
        className={
          bad
            ? "text-destructive text-xs font-semibold"
            : ok === true
              ? "text-success text-xs font-semibold"
              : "text-xs font-semibold"
        }
      >
        {ev.replaceAll("_", " ")}
        {ok === true ? " ✓" : ok === false ? " ✗" : ""}
      </span>
      {bits.map((b, j) => (
        <span key={j} className="text-xs">
          {b}
        </span>
      ))}
      {detail && (
        <span className="text-muted-foreground w-full text-xs">↳ {detail}</span>
      )}
    </div>
  );
}

/**
 * The full run report - the event timeline + evidence, replay outcomes, and the
 * recorded capability. Rendered inline on `/runs/[id]` once the run has finished
 * (in place of the now-dead live view).
 */
export function RunReport({
  id,
  showSummary = true,
}: {
  id: string;
  /** the run page already has a header + stats bar, so it hides this card */
  showSummary?: boolean;
}) {
  const [lightbox, setLightbox] = useState<string | null>(null);
  const { data: rep } = useQuery<RunReportData>({
    queryKey: ["report", id],
    queryFn: () => api.report(id),
  });

  if (!rep)
    return (
      <p className="text-muted-foreground text-sm">Assembling report…</p>
    );
  const run = rep.run;
  const { blocks, orphanShots, docs } = buildBlocks(rep.timeline, rep.evidence);
  const hasEvidence = rep.evidence.some((e) => e.endsWith(".png"));

  const banner =
    run.status === "business_outcome"
      ? {
          cls: "border-warning/40 bg-warning/10 text-warning",
          title: "Business outcome — a legitimate answer, not a failure",
        }
      : run.status === "dead_end" || run.status === "failed"
        ? {
            cls: "border-destructive/40 bg-destructive/10 text-destructive",
            title:
              run.status === "dead_end" ? "Dead end" : "Run failed",
          }
        : null;

  return (
    <div className="space-y-4">
      {banner && (
        <div className={`rounded-lg border p-3 text-sm ${banner.cls}`}>
          <p className="font-medium">{banner.title}</p>
          {run.detail && (
            <p className="mt-0.5 opacity-90">{run.detail}</p>
          )}
        </div>
      )}
      {showSummary && (
        <Card className="print-card">
          <CardHeader>
            <CardTitle className="flex flex-wrap items-center gap-2 text-base">
              {run.name ?? run.goal ?? run.mode}
              <StatusBadge status={run.status} />
            </CardTitle>
          </CardHeader>
          <CardContent className="text-sm">
            <dl className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
              <Row k="Run" v={<code>{run.run_id}</code>} />
              <Row k="Mode" v={run.mode} />
              <Row
                k="Target"
                v={<code className="text-xs">{run.app_target}</code>}
              />
              <Row k="Steps" v={String(run.step_count)} />
              <Row k="Detail" v={run.detail ?? "-"} />
              {run.artifact_id && (
                <Row
                  k="Artifact"
                  v={
                    <code className="text-xs">
                      {run.artifact_id.slice(0, 12)} v{run.artifact_version}
                    </code>
                  }
                />
              )}
            </dl>
          </CardContent>
        </Card>
      )}

      {/* one table row per step: step no · action(s) · evidence */}
      <Card className="print-card min-w-0">
        <CardHeader>
          <CardTitle className="text-base">
            Timeline{hasEvidence ? " & evidence" : ""}
          </CardTitle>
        </CardHeader>
        <CardContent className="overflow-x-auto">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead className="w-14">Step</TableHead>
                <TableHead>Action</TableHead>
                {hasEvidence && (
                  <TableHead className="w-[240px]">Evidence</TableHead>
                )}
              </TableRow>
            </TableHeader>
            <TableBody>
              {blocks.map((b, i) => (
                <TableRow key={i} className="align-top">
                  <TableCell className="text-muted-foreground pt-2 font-mono text-xs tabular-nums">
                    {b.step != null ? `s${b.step}` : "·"}
                  </TableCell>
                  <TableCell className="py-2">
                    <div className="space-y-0.5">
                      {b.events.map((e, j) => (
                        <TimelineRow key={j} e={e} />
                      ))}
                    </div>
                  </TableCell>
                  {hasEvidence && (
                    <TableCell className="py-2">
                      <div className="space-y-2">
                        {b.shots.map((s) => (
                          <Thumb key={s} src={s} onOpen={setLightbox} />
                        ))}
                      </div>
                    </TableCell>
                  )}
                </TableRow>
              ))}
            </TableBody>
          </Table>

          {orphanShots.length > 0 && (
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-4">
              {orphanShots.map((s) => (
                <Thumb key={s} src={s} onOpen={setLightbox} />
              ))}
            </div>
          )}
          {docs.length > 0 && (
            <ul className="text-muted-foreground mt-3 space-y-0.5 text-xs">
              {docs.map((d) => (
                <li key={d}>
                  <a
                    href={API_BASE + d}
                    target="_blank"
                    rel="noreferrer"
                    className="underline underline-offset-2"
                  >
                    {d.split("/").pop()}
                  </a>
                </li>
              ))}
            </ul>
          )}
        </CardContent>
      </Card>

      <Dialog open={!!lightbox} onOpenChange={(o) => !o && setLightbox(null)}>
        <DialogContent className="w-auto max-w-[95vw] border-none bg-transparent p-0 shadow-none sm:max-w-[95vw]">
          <DialogTitle className="sr-only">Evidence screenshot</DialogTitle>
          {lightbox && (
            <a href={API_BASE + lightbox} target="_blank" rel="noreferrer">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={API_BASE + lightbox}
                alt={lightbox}
                className="max-h-[90vh] w-auto max-w-full rounded-lg border shadow-2xl"
              />
            </a>
          )}
        </DialogContent>
      </Dialog>

      {rep.replays.length > 0 && (
        <Card className="print-card">
          <CardHeader>
            <CardTitle className="text-base">Replay invocations</CardTitle>
          </CardHeader>
          <CardContent className="space-y-3 text-sm">
            {rep.replays.map((r, i) => {
              const rr = r as {
                params?: unknown;
                business_outcome_code?: string;
                outputs?: unknown;
                recovered_conditions?: string[];
                failure_detail?: {
                  step_index: number;
                  expected: string;
                  observed: string;
                } | null;
              };
              return (
                <div key={i} className="space-y-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <OutcomeBadge outcome={r.outcome} />
                    <code className="text-xs">
                      {JSON.stringify(rr.params ?? {})}
                    </code>
                    {rr.business_outcome_code && (
                      <code className="text-warning text-xs">
                        {rr.business_outcome_code}
                      </code>
                    )}
                  </div>
                  {rr.outputs != null &&
                    Object.keys(rr.outputs as object).length > 0 && (
                      <pre className="bg-muted overflow-x-auto rounded p-2 text-[11px]">
                        {JSON.stringify(rr.outputs, null, 2)}
                      </pre>
                    )}
                  {!!rr.recovered_conditions?.length && (
                    <p className="text-muted-foreground text-xs">
                      recovered: {rr.recovered_conditions.join(", ")}
                    </p>
                  )}
                  {rr.failure_detail && (
                    <p className="text-destructive text-xs">
                      step {rr.failure_detail.step_index}: expected{" "}
                      {rr.failure_detail.expected}, observed{" "}
                      {rr.failure_detail.observed}
                    </p>
                  )}
                </div>
              );
            })}
          </CardContent>
        </Card>
      )}

      {rep.artifact && (
        <Card className="print-card">
          <CardHeader>
            <CardTitle className="text-base">Capability artifact</CardTitle>
          </CardHeader>
          <CardContent>
            <ArtifactView artifact={rep.artifact as never} />
          </CardContent>
        </Card>
      )}
    </div>
  );
}

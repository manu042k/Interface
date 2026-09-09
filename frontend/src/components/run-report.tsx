"use client";

import { useQuery } from "@tanstack/react-query";
import { api, API_BASE, type RunReport as RunReportData } from "@/lib/api";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
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

function Shot({ src }: { src: string }) {
  return (
    <a
      href={API_BASE + src}
      target="_blank"
      rel="noreferrer"
      className="block"
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={API_BASE + src}
        alt={src}
        className="max-h-64 w-full rounded border object-contain object-top transition-opacity hover:opacity-80"
      />
    </a>
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
    <li className="border-border/40 flex flex-wrap items-baseline gap-x-2 border-b py-1 last:border-0">
      <span className="text-muted-foreground w-12 shrink-0 text-xs tabular-nums">
        {e.step != null ? `s${String(e.step)}` : "·"}
      </span>
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
        <span className="text-muted-foreground w-full pl-14 text-xs">
          ↳ {detail}
        </span>
      )}
    </li>
  );
}

/**
 * The full run report - the summary, the event timeline, replay outcomes, the
 * recorded capability, and the evidence gallery. Rendered on its own page
 * (`/runs/[id]/report`, with print controls) and inline on the run page once the
 * run has finished (in place of the now-dead live view).
 */
export function RunReport({
  id,
  showSummary = true,
}: {
  id: string;
  /** the run page already has a header + stats bar, so it hides this card */
  showSummary?: boolean;
}) {
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

  return (
    <div className="space-y-4">
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

      {/* one row per step: its events on the left, its screenshot on the right */}
      <Card className="print-card min-w-0">
        <CardHeader>
          <CardTitle className="text-base">
            Timeline{rep.evidence.length > 0 ? " & evidence" : ""}
          </CardTitle>
        </CardHeader>
        <CardContent>
          <ol className="divide-border/50 divide-y text-sm">
            {blocks.map((b, i) => (
              <li
                key={i}
                className="grid gap-3 py-2 first:pt-0 last:pb-0 lg:grid-cols-[1.7fr_1fr]"
              >
                <div className="min-w-0 space-y-1">
                  {b.events.map((e, j) => (
                    <TimelineRow key={j} e={e} />
                  ))}
                </div>
                {b.shots.length > 0 && (
                  <div className="min-w-0 space-y-2 lg:justify-self-end">
                    {b.shots.map((s) => (
                      <Shot key={s} src={s} />
                    ))}
                  </div>
                )}
              </li>
            ))}
          </ol>

          {orphanShots.length > 0 && (
            <div className="mt-3 grid grid-cols-2 gap-2 sm:grid-cols-3">
              {orphanShots.map((s) => (
                <Shot key={s} src={s} />
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

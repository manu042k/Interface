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

      <Card className="print-card">
        <CardHeader>
          <CardTitle className="text-base">Timeline</CardTitle>
        </CardHeader>
        <CardContent>
          <ol className="space-y-1.5 text-sm">
            {rep.timeline.map((e, i) => (
              <li key={i} className="flex flex-wrap items-baseline gap-x-2">
                {e.step != null && (
                  <span className="text-muted-foreground text-xs">
                    step {String(e.step)}
                  </span>
                )}
                <span className="font-medium">
                  {String(e.event).replaceAll("_", " ")}
                </span>
                {["tool", "verdict", "action_type", "code", "status", "rule"].map(
                  (k) =>
                    e[k] ? (
                      <code key={k} className="text-xs">
                        {String(e[k])}
                      </code>
                    ) : null,
                )}
                {typeof e.reasoning === "string" && (
                  <span className="text-muted-foreground w-full text-xs italic">
                    {e.reasoning}
                  </span>
                )}
              </li>
            ))}
          </ol>
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

      {rep.evidence.length > 0 && (
        <Card className="print-card">
          <CardHeader>
            <CardTitle className="text-base">Evidence</CardTitle>
          </CardHeader>
          <CardContent>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
              {rep.evidence
                .filter((e) => e.endsWith(".png"))
                .map((e) => (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img
                    key={e}
                    src={API_BASE + e}
                    alt={e}
                    className="rounded border"
                  />
                ))}
            </div>
            {rep.evidence.some((e) => !e.endsWith(".png")) && (
              <ul className="text-muted-foreground mt-2 text-xs">
                {rep.evidence
                  .filter((e) => !e.endsWith(".png"))
                  .map((e) => (
                    <li key={e}>{e}</li>
                  ))}
              </ul>
            )}
          </CardContent>
        </Card>
      )}
    </div>
  );
}

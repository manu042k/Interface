"use client";

import { useParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import { Download, Printer } from "lucide-react";
import { api, API_BASE } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { OutcomeBadge, StatusBadge } from "@/components/badges";
import { ArtifactView } from "@/components/artifact-view";

export default function ReportPage() {
  const { id } = useParams<{ id: string }>();
  const { data: rep } = useQuery({
    queryKey: ["report", id],
    queryFn: () => api.report(id),
  });

  if (!rep) return <p className="text-muted-foreground">Assembling report…</p>;
  const run = rep.run;

  return (
    <div className="space-y-5">
      <header className="no-print flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Run report</h1>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => window.print()}>
            <Printer className="mr-1.5 h-4 w-4" /> Print / Save PDF
          </Button>
          <Button asChild size="sm">
            <a href={api.reportMdUrl(id)} download={`report-${id}.md`}>
              <Download className="mr-1.5 h-4 w-4" /> Download .md
            </a>
          </Button>
        </div>
      </header>

      <Card className="print-card">
        <CardHeader>
          <CardTitle className="flex flex-wrap items-center gap-2 text-base">
            {run.goal ?? run.mode}
            <StatusBadge status={run.status} />
          </CardTitle>
        </CardHeader>
        <CardContent className="text-sm">
          <dl className="grid gap-x-6 gap-y-1 sm:grid-cols-2">
            <Row k="Run" v={<code>{run.run_id}</code>} />
            <Row k="Mode" v={run.mode} />
            <Row k="Target" v={<code className="text-xs">{run.app_target}</code>} />
            <Row k="Steps" v={String(run.step_count)} />
            <Row k="Detail" v={run.detail ?? "-"} />
            {run.artifact_id && (
              <Row
                k="Artifact"
                v={
                  <code className="text-xs">
                    {run.artifact_id} v{run.artifact_version}
                  </code>
                }
              />
            )}
          </dl>
        </CardContent>
      </Card>

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
                {["tool", "verdict", "action_type", "code", "status"].map(
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
          <CardContent className="space-y-2 text-sm">
            {rep.replays.map((r, i) => (
              <div key={i} className="flex flex-wrap items-center gap-2">
                <OutcomeBadge outcome={r.outcome} />
                <code className="text-xs">
                  {JSON.stringify((r as { params?: unknown }).params ?? {})}
                </code>
                {(r as { business_outcome_code?: string })
                  .business_outcome_code && (
                  <code className="text-warning text-xs">
                    {
                      (r as { business_outcome_code?: string })
                        .business_outcome_code
                    }
                  </code>
                )}
                {(r as { outputs?: unknown }).outputs ? (
                  <code className="text-xs">
                    {JSON.stringify((r as { outputs?: unknown }).outputs)}
                  </code>
                ) : null}
              </div>
            ))}
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
            <ul className="text-muted-foreground mt-2 text-xs">
              {rep.evidence
                .filter((e) => !e.endsWith(".png"))
                .map((e) => (
                  <li key={e}>{e}</li>
                ))}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Row({ k, v }: { k: string; v: React.ReactNode }) {
  return (
    <div className="flex gap-2 py-0.5">
      <dt className="text-muted-foreground w-24 shrink-0">{k}</dt>
      <dd className="min-w-0">{v}</dd>
    </div>
  );
}

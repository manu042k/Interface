"use client";

import { Fragment, useEffect, useState } from "react";
import { Check, ChevronDown, Maximize2, X } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { api, API_BASE, type RunReport as RunReportData } from "@/lib/api";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogTitle,
} from "@/components/ui/dialog";
import { OutcomeBadge, StatusBadge } from "@/components/badges";
import { ArtifactView } from "@/components/artifact-view";
import { Separator } from "@/components/ui/separator";
import { isSensitiveKey, maskSecret, sentenceCase } from "@/lib/text";

function pathOf(url: unknown): string {
  if (typeof url !== "string" || !url) return "";
  try {
    const u = new URL(url);
    return u.pathname + u.search;
  } catch {
    return url;
  }
}

function stepOfEvidence(path: string): number | null {
  const m = path.match(/\/step(\d+)-/);
  return m ? Number(m[1]) : null;
}

function pickEvent(
  events: Record<string, unknown>[],
  name: string,
): Record<string, unknown> | undefined {
  return [...events].reverse().find((e) => e.event === name);
}

type ArtifactStep = {
  step_index: number;
  action_type?: string;
  description?: string;
  value_binding?: { param?: string | null; literal?: string | null } | null;
};

type StepBlock = {
  step: number | null;
  events: Record<string, unknown>[];
  shots: string[];
};

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
      className="group focus-visible:ring-ring relative block w-full overflow-hidden rounded-lg border bg-white shadow-sm transition hover:border-primary/50 focus-visible:ring-2 focus-visible:outline-none"
      title="Click to enlarge"
      aria-label="Enlarge screenshot"
    >
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={API_BASE + src}
        alt=""
        className="h-40 w-full object-cover object-top"
      />
      <span className="pointer-events-none absolute inset-0 flex items-end justify-center bg-gradient-to-t from-black/45 to-transparent p-1.5 text-xs font-medium text-white opacity-0 transition group-hover:opacity-100">
        <Maximize2 className="mr-1 h-3 w-3" /> Enlarge
      </span>
    </button>
  );
}

function Meta({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[7rem_minmax(0,1fr)] items-baseline gap-x-3 gap-y-1 text-sm">
      <dt className="text-muted-foreground font-heading text-xs font-medium tracking-wide">
        {label}
      </dt>
      <dd className="text-foreground min-w-0 break-words">{children}</dd>
    </div>
  );
}

function actionTone(type: string) {
  const t = type.toLowerCase();
  if (t.includes("assert") || t.includes("check"))
    return "bg-success/12 text-success";
  if (t.includes("type") || t.includes("fill") || t.includes("enter"))
    return "bg-primary/12 text-primary";
  if (t.includes("select") || t.includes("choose") || t.includes("option"))
    return "bg-warning/12 text-warning";
  if (t.includes("click") || t.includes("press") || t.includes("tap"))
    return "bg-foreground/8 text-foreground";
  return "bg-muted text-muted-foreground";
}

function outcomeTint(outcome?: string) {
  if (outcome === "success" || outcome === "recoverable_then_success")
    return "bg-success/8";
  if (outcome === "business_outcome") return "bg-warning/8";
  if (outcome === "hard_failure") return "bg-destructive/8";
  return "";
}

type ReplayRow = {
  invocation_id?: string;
  outcome?: string;
  params?: Record<string, unknown>;
  business_outcome_code?: string;
  outputs?: Record<string, unknown> | null;
  recovered_conditions?: string[];
  steps_executed?: number;
  duration_seconds?: number;
  failure_detail?: {
    step_index: number;
    expected: string;
    observed: string;
  } | null;
};

function ResultCard({
  replay,
  sensitive,
  title,
}: {
  replay: ReplayRow;
  sensitive: Set<string>;
  title: string;
}) {
  const params = Object.entries(replay.params ?? {});
  const outputs = Object.entries(replay.outputs ?? {});
  return (
    <Card className="print-card gap-0 overflow-hidden py-0">
      <CardHeader className={`px-6 py-4 ${outcomeTint(replay.outcome)}`}>
        <CardTitle className="flex flex-wrap items-center gap-2 text-base">
          {title}
          <OutcomeBadge outcome={replay.outcome} />
          {replay.business_outcome_code && (
            <code className="bg-warning/12 text-warning rounded px-1.5 py-0.5 text-xs">
              {replay.business_outcome_code}
            </code>
          )}
          <span className="text-muted-foreground ml-auto text-sm font-normal tabular-nums">
            {replay.steps_executed != null && `${replay.steps_executed} steps`}
            {replay.duration_seconds != null &&
              ` · ${replay.duration_seconds.toFixed(1)}s`}
          </span>
        </CardTitle>
      </CardHeader>
      <Separator />
      <CardContent className="space-y-4 px-6 py-4">
        {params.length > 0 && (
          <div>
            <p className="font-heading mb-2 text-sm font-semibold tracking-tight">
              Inputs
            </p>
            <dl className="space-y-1.5">
              {params.map(([k, v], i) => (
                <Fragment key={k}>
                  {i > 0 && <Separator className="opacity-60" />}
                  <Meta label={sentenceCase(k)}>
                    {isSensitiveKey(k) || sensitive.has(k) ? (
                      <span className="text-muted-foreground">{maskSecret(v)}</span>
                    ) : (
                      <span className="font-mono text-xs">
                        {typeof v === "string" || typeof v === "number"
                          ? String(v)
                          : JSON.stringify(v)}
                      </span>
                    )}
                  </Meta>
                </Fragment>
              ))}
            </dl>
          </div>
        )}

        {outputs.length > 0 && (
          <>
            {params.length > 0 && <Separator />}
            <div>
              <p className="font-heading mb-2 text-sm font-semibold tracking-tight">
                Outputs
              </p>
              <dl className="space-y-1.5">
                {outputs.map(([k, v], i) => (
                  <Fragment key={k}>
                    {i > 0 && <Separator className="opacity-60" />}
                    <Meta label={sentenceCase(k)}>
                      <span className="font-mono text-xs">
                        {typeof v === "string" || typeof v === "number"
                          ? String(v)
                          : JSON.stringify(v)}
                      </span>
                    </Meta>
                  </Fragment>
                ))}
              </dl>
            </div>
          </>
        )}

        {!!replay.recovered_conditions?.length && (
          <p className="text-muted-foreground text-sm">
            Recovered:{" "}
            <span className="text-foreground">
              {replay.recovered_conditions.map(sentenceCase).join(", ")}
            </span>
          </p>
        )}

        {replay.failure_detail && (
          <div className="border-destructive/30 bg-destructive/5 text-destructive rounded-lg border px-3 py-2 text-sm">
            Step {replay.failure_detail.step_index}: expected{" "}
            <span className="font-medium">{replay.failure_detail.expected}</span>
            , observed{" "}
            <span className="font-medium">{replay.failure_detail.observed}</span>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function StepRow({
  block,
  artifact,
  onOpenShot,
}: {
  block: StepBlock;
  artifact?: ArtifactStep;
  onOpenShot: (src: string) => void;
}) {
  const action = pickEvent(block.events, "action") ?? block.events[block.events.length - 1];
  const loc = pickEvent(block.events, "locator_resolution");
  const guard = pickEvent(block.events, "guardrail");
  const check = pickEvent(block.events, "checkpoint");
  const bad = block.events.some(
    (e) =>
      e.ok === false ||
      e.verdict === "block" ||
      ["hard_failure", "stuck", "business_outcome"].includes(String(e.event)),
  );
  const warn = block.events.some(
    (e) =>
      e.drift_signal === true || String(e.event) === "recoverable_condition",
  );
  const actionType =
    (typeof action.action_type === "string" && action.action_type) ||
    (typeof action.tool === "string" && action.tool) ||
    artifact?.action_type ||
    String(action.event);
  const description =
    artifact?.description ||
    (typeof action.description === "string" && action.description) ||
    sentenceCase(actionType);
  const url = pathOf(action.url_after);
  const locator =
    (typeof loc?.matched_strategy === "string" && loc.matched_strategy) ||
    (typeof action.matched_strategy === "string" && action.matched_strategy) ||
    "";
  const drifted =
    loc?.drift_signal === true ||
    (typeof loc?.matched_rank === "number" && loc.matched_rank > 0);
  const [open, setOpen] = useState(bad);

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        className="hover:bg-muted/40 flex w-full items-start gap-3 px-4 py-3 text-left"
      >
        <span
          className={`mt-0.5 grid size-6 shrink-0 place-items-center rounded-md text-xs font-semibold ${
            bad
              ? "bg-destructive/12 text-destructive"
              : warn
                ? "bg-warning/12 text-warning"
                : "bg-muted text-muted-foreground"
          }`}
        >
          {block.step}
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
            <span
              className={`rounded-md px-1.5 py-0.5 text-xs font-semibold tracking-tight ${actionTone(actionType)}`}
            >
              {sentenceCase(actionType)}
            </span>
            {url && (
              <span className="text-muted-foreground truncate font-mono text-xs">
                {url}
              </span>
            )}
            <span
              className={`ml-auto inline-flex items-center gap-1 text-xs font-medium ${
                bad ? "text-destructive" : "text-success"
              }`}
            >
              {bad ? (
                <X className="h-3.5 w-3.5" />
              ) : (
                <Check className="h-3.5 w-3.5" />
              )}
              {bad ? "Failed" : "Ok"}
            </span>
          </div>
          <p className="text-muted-foreground mt-0.5 line-clamp-2 text-sm leading-relaxed">
            {description}
          </p>
        </div>
        <ChevronDown
          className={`text-muted-foreground mt-1 h-4 w-4 shrink-0 transition-transform ${
            open ? "rotate-180" : ""
          }`}
        />
      </button>

      {open && (
      <div className="space-y-3 px-4 pt-0 pb-4 pl-[3.25rem]">
        <dl className="space-y-1.5 text-sm">
          {locator && (
            <Meta label="Matched">
              <code className="bg-muted rounded px-1.5 py-0.5 text-xs">
                {locator}
              </code>
              {drifted && (
                <span className="text-warning ml-2 text-xs font-medium">
                  Fallback #{String(loc?.matched_rank ?? "?")} (drift)
                </span>
              )}
            </Meta>
          )}
          {artifact?.value_binding?.param && (
            <Meta label="Input">
              <code className="text-xs">{artifact.value_binding.param}</code>
            </Meta>
          )}
          {guard && (
            <Meta label="Guardrail">
              <span
                className={
                  guard.verdict === "block" ||
                  guard.verdict === "require_confirmation"
                    ? "text-destructive font-medium"
                    : "text-foreground"
                }
              >
                {sentenceCase(String(guard.verdict ?? "allow"))}
              </span>
              {typeof guard.reason === "string" && (
                <span className="text-muted-foreground">
                  {" "}
                  · {guard.reason}
                </span>
              )}
            </Meta>
          )}
          {check && (
            <Meta label="Checkpoint">
              <span className={check.ok === false ? "text-destructive" : "text-success"}>
                {check.ok === false ? "Failed" : "Passed"}
              </span>
              {typeof check.description === "string" && (
                <span className="text-muted-foreground">
                  {" "}
                  · {check.description}
                </span>
              )}
            </Meta>
          )}
          {action.timed_out === true && (
            <Meta label="Timeout">
              <span className="text-destructive">Timed out</span>
            </Meta>
          )}
        </dl>

        {block.shots.length > 0 && (
          <div className="grid max-w-sm grid-cols-1 gap-2">
            {block.shots.map((s) => (
              <Thumb key={s} src={s} onOpen={onOpenShot} />
            ))}
          </div>
        )}
      </div>
      )}
    </div>
  );
}

/**
 * The full run report — outcome, step narrative, and the recorded capability.
 * Rendered inline on `/runs/[id]` once the run has finished.
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
  const [artifactOpen, setArtifactOpen] = useState(false);
  const { data: rep } = useQuery<RunReportData>({
    queryKey: ["report", id],
    queryFn: () => api.report(id),
  });

  // an un-reviewed draft is the point of the run — don't leave it collapsed
  useEffect(() => {
    if ((rep?.artifact as { status?: string } | null)?.status === "draft") {
      setArtifactOpen(true);
    }
  }, [rep]);

  if (!rep)
    return (
      <p className="text-muted-foreground text-sm">Assembling report…</p>
    );

  const run = rep.run;
  const { blocks, orphanShots, docs } = buildBlocks(rep.timeline, rep.evidence);
  const stepBlocks = blocks.filter((b) => b.step != null);
  const artifactSteps = (
    Array.isArray((rep.artifact as { steps?: ArtifactStep[] } | null)?.steps)
      ? ((rep.artifact as { steps: ArtifactStep[] }).steps)
      : []
  );
  const stepMeta = new Map(artifactSteps.map((s) => [s.step_index, s]));
  const sensitive = new Set(
    Object.entries(
      (
        rep.artifact as {
          input_schema?: { properties?: Record<string, { "x-sensitive"?: boolean }> };
        } | null
      )?.input_schema?.properties ?? {},
    )
      .filter(([, v]) => v?.["x-sensitive"])
      .map(([k]) => k),
  );
  const replays = (rep.replays ?? []) as ReplayRow[];
  const thisInvocation =
    replays.length === 1 && replays[0]?.invocation_id === id
      ? replays[0]
      : null;

  const art = rep.artifact as
    | { status?: string; duplicate_of?: string | null }
    | null;
  const needsReview = art?.status === "draft";
  const dupOf = art?.duplicate_of ?? null;

  const banner =
    run.status === "business_outcome"
      ? {
          cls: "border-warning/40 bg-warning/10 text-warning",
          title: "Business outcome — a legitimate answer, not a failure",
        }
      : run.status === "dead_end" || run.status === "failed"
        ? {
            cls: "border-destructive/40 bg-destructive/10 text-destructive",
            title: run.status === "dead_end" ? "Dead end" : "Run failed",
          }
        : null;

  return (
    <div className="space-y-5">
      {banner && (
        <div className={`rounded-xl border p-4 text-sm ${banner.cls}`}>
          <p className="font-heading font-semibold">{banner.title}</p>
          {run.detail && <p className="mt-1 opacity-90">{run.detail}</p>}
        </div>
      )}

      {showSummary && (
        <Card className="print-card">
          <CardHeader>
            <CardTitle className="flex flex-wrap items-center gap-2 text-base">
              {sentenceCase(run.name ?? run.goal ?? run.mode)}
              <StatusBadge status={run.status} />
            </CardTitle>
          </CardHeader>
          <CardContent className="space-y-1.5 text-sm">
            <Meta label="Run">
              <code className="text-xs">{run.run_id}</code>
            </Meta>
            <Meta label="Mode">{sentenceCase(run.mode)}</Meta>
            <Meta label="Target">
              <code className="text-xs">{run.app_target}</code>
            </Meta>
            <Meta label="Steps">{String(run.step_count)}</Meta>
            <Meta label="Detail">{run.detail ?? "—"}</Meta>
            {run.artifact_id && (
              <Meta label="Artifact">
                <code className="text-xs">
                  {run.artifact_id.slice(0, 12)} v{run.artifact_version}
                </code>
              </Meta>
            )}
          </CardContent>
        </Card>
      )}

      {thisInvocation ? (
        <ResultCard
          replay={thisInvocation}
          sensitive={sensitive}
          title="Result"
        />
      ) : (
        replays.length > 0 && (
          <div className="space-y-3">
            <h2 className="font-heading text-base font-semibold tracking-tight">
              Replay invocations
              <span className="text-muted-foreground ml-1.5 text-sm font-normal">
                · {replays.length}
              </span>
            </h2>
            {replays.map((r, i) => (
              <ResultCard
                key={r.invocation_id ?? i}
                replay={r}
                sensitive={sensitive}
                title={`Invocation ${i + 1}`}
              />
            ))}
          </div>
        )
      )}

      <Card className="print-card min-w-0 gap-0 overflow-hidden py-0">
        <CardHeader className="px-6 py-4">
          <CardTitle className="text-base">
            Steps
            <span className="text-muted-foreground ml-1.5 text-sm font-normal">
              · {stepBlocks.length}
            </span>
          </CardTitle>
        </CardHeader>
        <Separator />
        <CardContent className="px-0 py-0">
          {stepBlocks.length === 0 ? (
            <p className="text-muted-foreground px-4 py-6 text-sm">
              No steps were recorded.
            </p>
          ) : (
            <div>
              {stepBlocks.map((b, i) => (
                <Fragment key={b.step}>
                  {i > 0 && <Separator />}
                  <StepRow
                    block={b}
                    artifact={
                      b.step != null ? stepMeta.get(b.step) : undefined
                    }
                    onOpenShot={setLightbox}
                  />
                </Fragment>
              ))}
            </div>
          )}

          {orphanShots.length > 0 && (
            <div className="grid grid-cols-2 gap-2 px-4 py-4 sm:grid-cols-4">
              {orphanShots.map((s) => (
                <Thumb key={s} src={s} onOpen={setLightbox} />
              ))}
            </div>
          )}
          {docs.length > 0 && (
            <ul className="text-muted-foreground space-y-0.5 px-4 py-3 text-xs">
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

      {rep.artifact && (
        <Card className="print-card">
          <button
            type="button"
            onClick={() => setArtifactOpen((v) => !v)}
            className="hover:bg-muted/40 flex w-full items-center gap-2 rounded-xl px-6 py-4 text-left transition-colors"
            aria-expanded={artifactOpen}
          >
            <CardTitle className="flex flex-1 flex-wrap items-center gap-2 text-base">
              Recorded capability
              <span className="text-muted-foreground text-sm font-normal">
                {sentenceCase(run.name)}
                {run.artifact_version ? ` v${run.artifact_version}` : ""} ·{" "}
                {run.step_count} steps
              </span>
              {needsReview && (
                <span className="border-warning/40 bg-warning/10 text-warning rounded border px-1.5 py-0.5 text-[11px] font-medium">
                  Needs review
                </span>
              )}
              {dupOf && (
                <span className="text-muted-foreground text-xs font-normal">
                  · possible duplicate of {dupOf}
                </span>
              )}
            </CardTitle>
            <ChevronDown
              className={`text-muted-foreground h-4 w-4 shrink-0 transition-transform ${
                artifactOpen ? "rotate-180" : ""
              }`}
            />
          </button>
          {artifactOpen && (
            <>
              <Separator />
              <CardContent className="pt-5">
                <ArtifactView artifact={rep.artifact as never} />
              </CardContent>
            </>
          )}
        </Card>
      )}

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
    </div>
  );
}

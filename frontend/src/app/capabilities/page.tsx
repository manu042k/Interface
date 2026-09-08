"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  Loader2,
  Play,
  Sparkles,
  ArrowRight,
  Flag,
  RotateCcw,
  ChevronDown,
  Crosshair,
  CheckCircle2,
} from "lucide-react";
import { toast } from "sonner";
import { api, type Capability, type ReplayResult } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { OutcomeBadge, RiskBadge } from "@/components/badges";
import { Skeleton } from "@/components/ui/skeleton";

export default function CapabilitiesPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["capabilities"],
    queryFn: api.capabilities,
    refetchInterval: 4000,
  });
  const [sel, setSel] = useState<Capability | null>(null);

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">Capabilities</h1>
        <p className="text-muted-foreground mt-1">
          Approved, agent-invocable. Invoking runs a deterministic replay — no
          model in the loop.
        </p>
      </header>

      {isLoading && (
        <div className="space-y-3">
          <Skeleton className="h-44 w-full" />
          <Skeleton className="h-44 w-full" />
        </div>
      )}

      {data?.length === 0 && (
        <Card>
          <CardContent className="text-muted-foreground py-10 text-center text-sm">
            No approved capabilities yet — run a discovery, then approve it in
            Review.
          </CardContent>
        </Card>
      )}

      <div className="space-y-4">
        {data?.map((c) => (
          <CapabilityCard key={c.artifact_id} cap={c} onInvoke={() => setSel(c)} />
        ))}
      </div>

      <InvokeDialog cap={sel} onClose={() => setSel(null)} />
    </div>
  );
}

function CapabilityCard({
  cap,
  onInvoke,
}: {
  cap: Capability;
  onInvoke: () => void;
}) {
  const [showTargets, setShowTargets] = useState(false);
  return (
    <Card>
      <CardContent className="space-y-3.5 pt-5">
        <div className="flex flex-wrap items-center gap-2">
          <code className="text-sm font-semibold">{cap.name}</code>
          <RiskBadge risk={cap.risk_class} />
          <span className="text-muted-foreground text-xs">
            v{cap.version}
            {cap.supersedes != null && ` · supersedes v${cap.supersedes}`}
            {cap.older_versions > 0 && ` · ${cap.older_versions} older`}
          </span>
          <span className="text-muted-foreground text-xs">
            · {cap.vendor_app_id}
          </span>
          {cap.confirmations > 0 && (
            <span
              className="bg-primary/10 text-primary inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs font-medium"
              title={`${cap.confirmations} later discovery run${cap.confirmations === 1 ? "" : "s"} reproduced this exact flow — no new version needed`}
            >
              <CheckCircle2 className="h-3 w-3" />
              confirmed {cap.confirmations}×
            </span>
          )}
          <Button
            size="sm"
            className="ml-auto"
            onClick={onInvoke}
          >
            <Play className="mr-1.5 h-3.5 w-3.5" /> Invoke
          </Button>
        </div>

        <p className="text-sm leading-relaxed">
          {cap.summary_source === "model" && (
            <Sparkles className="text-primary mr-1 inline h-3.5 w-3.5 align-[-2px]" />
          )}
          {cap.summary}
        </p>

        <div className="grid gap-3 sm:grid-cols-2">
          <div>
            <div className="text-muted-foreground mb-1 text-xs font-medium uppercase">
              Takes
            </div>
            <div className="flex flex-wrap gap-1.5">
              {cap.inputs.length === 0 && (
                <span className="text-muted-foreground text-xs">no inputs</span>
              )}
              {cap.inputs.map((p) => (
                <code
                  key={p.name}
                  className="bg-muted rounded px-1.5 py-0.5 text-xs"
                >
                  {p.name}
                  <span className="text-muted-foreground"> : {p.type}</span>
                  {p.sensitive && (
                    <span className="text-warning"> · sensitive</span>
                  )}
                </code>
              ))}
            </div>
          </div>
          <div>
            <div className="text-muted-foreground mb-1 text-xs font-medium uppercase">
              Returns
            </div>
            <div className="flex flex-wrap gap-1.5">
              {cap.outputs.length === 0 && (
                <span className="text-muted-foreground text-xs">no outputs</span>
              )}
              {cap.outputs.map((o) => (
                <code
                  key={o.field}
                  className="bg-muted rounded px-1.5 py-0.5 text-xs"
                >
                  {o.field}
                  <span className="text-muted-foreground"> : {o.shape}</span>
                </code>
              ))}
            </div>
          </div>
        </div>

        <div>
          <div className="text-muted-foreground mb-1.5 flex items-center justify-between text-xs font-medium uppercase">
            <span>Steps ({cap.steps.length})</span>
            <button
              type="button"
              onClick={() => setShowTargets((v) => !v)}
              className="hover:text-foreground flex items-center gap-1 normal-case"
            >
              <Crosshair className="h-3 w-3" />
              how each step is targeted
              <ChevronDown
                className={`h-3 w-3 transition-transform ${showTargets ? "rotate-180" : ""}`}
              />
            </button>
          </div>
          <ol className="flex flex-wrap items-center gap-x-1 gap-y-1.5 text-xs">
            {cap.steps.map((s, i) => (
              <li key={s.i} className="flex items-center gap-1">
                {i > 0 && (
                  <ArrowRight className="text-muted-foreground h-3 w-3 shrink-0" />
                )}
                <span
                  className="bg-accent rounded px-1.5 py-0.5"
                  title={s.description}
                >
                  <span className="font-medium">{s.action}</span>
                  {s.target && (
                    <span className="text-muted-foreground"> {s.target}</span>
                  )}
                  {s.output && (
                    <span className="text-muted-foreground"> → {s.output}</span>
                  )}
                </span>
              </li>
            ))}
          </ol>

          {showTargets && (
            <div className="border-border/60 mt-2.5 space-y-2.5 rounded-lg border p-3 text-xs">
              {cap.steps.map((s) => (
                <div key={s.i}>
                  <div className="flex flex-wrap items-baseline gap-1.5">
                    <span className="text-muted-foreground">{s.i}</span>
                    <span className="font-medium">{s.action}</span>
                    <span className="text-muted-foreground">
                      {s.description}
                    </span>
                    <span className="text-muted-foreground">
                      · {s.idempotent ? "idempotent" : "NON-idempotent"}
                    </span>
                  </div>
                  {s.locators.length > 0 ? (
                    <ul className="mt-1 space-y-1">
                      {s.locators.map((l, li) => (
                        <li key={li} className="text-muted-foreground">
                          <span className="text-foreground">
                            rank {l.rank} · {l.kind}
                          </span>{" "}
                          <code>{JSON.stringify(l.params)}</code>
                          <br />
                          <span className="opacity-80">↳ {l.rationale}</span>
                        </li>
                      ))}
                    </ul>
                  ) : (
                    <p className="text-muted-foreground mt-1">
                      {s.checkpoint
                        ? `checkpoint: ${JSON.stringify(s.checkpoint)}`
                        : "no element — control/assertion step"}
                    </p>
                  )}
                </div>
              ))}
              <p className="text-muted-foreground border-border/60 border-t pt-2 opacity-80">
                Replay tries these strategies top-down and uses the first that
                resolves to one visible element — no model. A match below rank 0
                is logged as a drift signal.
              </p>
            </div>
          )}
        </div>

        {(cap.handles.business_outcomes.length > 0 ||
          cap.handles.recoverable.length > 0) && (
          <div>
            <div className="text-muted-foreground mb-1 text-xs font-medium uppercase">
              Handles
            </div>
            <div className="flex flex-wrap gap-1.5">
              {cap.handles.business_outcomes.map((o) => (
                <span
                  key={o.code}
                  title={o.message}
                  className="bg-warning/12 text-warning inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs"
                >
                  <Flag className="h-3 w-3" />
                  {o.code}
                </span>
              ))}
              {cap.handles.recoverable.map((r) => (
                <span
                  key={r}
                  className="bg-muted text-muted-foreground inline-flex items-center gap-1 rounded px-1.5 py-0.5 text-xs"
                >
                  <RotateCcw className="h-3 w-3" />
                  {r}
                </span>
              ))}
            </div>
          </div>
        )}

        <p className="text-muted-foreground border-border/60 border-t pt-2.5 text-xs">
          recorded from run{" "}
          <code>
            {cap.provenance.created_from_run_id?.slice(0, 8) ?? "—"}
          </code>
          {cap.provenance.reviewed_by && ` · approved by ${cap.provenance.reviewed_by}`}
        </p>
      </CardContent>
    </Card>
  );
}

function InvokeDialog({
  cap,
  onClose,
}: {
  cap: Capability | null;
  onClose: () => void;
}) {
  const props = cap?.inputs ?? [];
  const [params, setParams] = useState<Record<string, string>>({});
  const [target, setTarget] = useState("http://localhost:8799/search");
  const [result, setResult] = useState<ReplayResult | null>(null);

  const mut = useMutation({
    mutationFn: () => api.invoke(cap!.artifact_id, cap!.version, target, params),
    onSuccess: (r) => setResult(r),
    onError: (e) => toast.error(String((e as Error).message)),
  });

  return (
    <Dialog
      open={!!cap}
      onOpenChange={(o) => {
        if (!o) {
          onClose();
          setResult(null);
          setParams({});
        }
      }}
    >
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle className="font-mono text-sm">
            Invoke — {cap?.name} v{cap?.version}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label>Target</Label>
            <Input value={target} onChange={(e) => setTarget(e.target.value)} />
          </div>
          {props.map((p) => (
            <div key={p.name} className="space-y-1.5">
              <Label>
                {p.name}{" "}
                {p.sensitive && (
                  <span className="text-warning text-xs">(sensitive)</span>
                )}
              </Label>
              <Input
                value={params[p.name] ?? ""}
                placeholder={p.example ? String(p.example) : undefined}
                onChange={(e) =>
                  setParams((s) => ({ ...s, [p.name]: e.target.value }))
                }
              />
            </div>
          ))}
          <Button
            onClick={() => mut.mutate()}
            disabled={mut.isPending}
            className="w-full"
          >
            {mut.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Run deterministic replay
          </Button>

          {result && (
            <div className="space-y-2 rounded-lg border p-3">
              <div className="flex items-center gap-2">
                <OutcomeBadge outcome={result.outcome} />
                {result.business_outcome_code && (
                  <code className="text-warning text-xs">
                    {result.business_outcome_code}
                  </code>
                )}
                {result.recovered_conditions?.length ? (
                  <span className="text-muted-foreground text-xs">
                    recovered: {result.recovered_conditions.join(", ")}
                  </span>
                ) : null}
              </div>
              <pre className="overflow-auto rounded bg-black/5 p-2 text-xs">
                {JSON.stringify(
                  result.outputs ?? result.failure_detail ?? {},
                  null,
                  2,
                )}
              </pre>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

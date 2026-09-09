"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import {
  Loader2,
  Play,
  Sparkles,
  Flag,
  RotateCcw,
  Crosshair,
  CheckCircle2,
  ShieldCheck,
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
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Accordion,
  AccordionItem,
  AccordionTrigger,
  AccordionContent,
} from "@/components/ui/accordion";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";

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
        <div className="grid gap-4 sm:grid-cols-2">
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
          <Skeleton className="h-40 w-full" />
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

      <div className="grid gap-4 sm:grid-cols-2">
        {data?.map((c) => (
          <CapabilityCard key={c.artifact_id} cap={c} onOpen={() => setSel(c)} />
        ))}
      </div>

      <CapabilityDetail cap={sel} onClose={() => setSel(null)} />
    </div>
  );
}

/* ---- compact card ---------------------------------------------------- */

function CapabilityCard({
  cap,
  onOpen,
}: {
  cap: Capability;
  onOpen: () => void;
}) {
  return (
    <Card
      onClick={onOpen}
      className="hover:border-primary/50 cursor-pointer transition-colors"
    >
      <CardContent className="flex h-full flex-col gap-2.5 pt-5">
        <div className="flex items-start gap-2">
          <code className="text-sm font-semibold break-all">{cap.name}</code>
          <RiskBadge risk={cap.risk_class} />
        </div>

        <p className="text-muted-foreground line-clamp-2 text-sm leading-relaxed">
          {cap.summary_source === "model" && (
            <Sparkles className="text-primary mr-1 inline h-3.5 w-3.5 align-[-2px]" />
          )}
          {cap.summary}
        </p>

        <div className="text-muted-foreground mt-auto flex flex-wrap items-center gap-x-2.5 gap-y-1 pt-1 text-xs">
          <span>{cap.steps.length} steps</span>
          <span>·</span>
          <span>
            {cap.inputs.length} in / {cap.outputs.length} out
          </span>
          <span>·</span>
          <span className="font-mono">
            v{cap.version}
            {cap.older_versions > 0 && ` +${cap.older_versions}`}
          </span>
          {cap.confirmations > 0 && (
            <span className="text-primary inline-flex items-center gap-1">
              <CheckCircle2 className="h-3 w-3" />
              {cap.confirmations}×
            </span>
          )}
          <Button
            size="sm"
            variant="outline"
            className="ml-auto"
            onClick={(e) => {
              e.stopPropagation();
              onOpen();
            }}
          >
            <Play className="mr-1.5 h-3.5 w-3.5" /> Invoke
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}

/* ---- detail modal -------------------------------------------------- */

function CapabilityDetail({
  cap,
  onClose,
}: {
  cap: Capability | null;
  onClose: () => void;
}) {
  const [tab, setTab] = useState("overview");

  return (
    <Dialog
      open={!!cap}
      onOpenChange={(o) => {
        if (!o) {
          onClose();
          setTab("overview");
        }
      }}
    >
      <DialogContent className="flex max-h-[88vh] w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
        {cap && (
          <>
            <DialogHeader className="space-y-2 border-b px-5 py-4 text-left">
              <div className="flex flex-wrap items-center gap-2">
                <DialogTitle className="font-mono text-sm">
                  {cap.name}
                </DialogTitle>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <span>
                      <RiskBadge risk={cap.risk_class} />
                    </span>
                  </TooltipTrigger>
                  <TooltipContent>
                    {cap.risk_class === "risky_irreversible"
                      ? "Performs an irreversible action — replay needs pre-authorization."
                      : "Safe and reversible — replay runs unattended."}
                  </TooltipContent>
                </Tooltip>
                {cap.confirmations > 0 && (
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Badge
                        variant="outline"
                        className="text-primary border-primary/30 gap-1"
                      >
                        <CheckCircle2 className="h-3 w-3" />
                        confirmed {cap.confirmations}×
                      </Badge>
                    </TooltipTrigger>
                    <TooltipContent>
                      {cap.confirmations} later discovery run
                      {cap.confirmations === 1 ? "" : "s"} reproduced this exact
                      flow — no new version needed.
                    </TooltipContent>
                  </Tooltip>
                )}
              </div>
              <p className="text-muted-foreground font-mono text-xs">
                v{cap.version}
                {cap.supersedes != null && ` · supersedes v${cap.supersedes}`}
                {cap.older_versions > 0 && ` · ${cap.older_versions} older`}
                {" · "}
                {cap.vendor_app_id} {cap.app_version}
              </p>
            </DialogHeader>

            <Tabs
              value={tab}
              onValueChange={setTab}
              className="flex min-h-0 flex-1 flex-col"
            >
              <TabsList className="mx-5 mt-3 w-fit">
                <TabsTrigger value="overview">Overview</TabsTrigger>
                <TabsTrigger value="steps">Steps ({cap.steps.length})</TabsTrigger>
                <TabsTrigger value="invoke">Invoke</TabsTrigger>
              </TabsList>

              {/* Overview */}
              <TabsContent
                value="overview"
                className="mt-0 min-h-0 flex-1 data-[state=inactive]:hidden"
              >
                <ScrollArea className="h-full">
                  <div className="space-y-4 px-5 py-4">
                    <p className="text-sm leading-relaxed">
                      {cap.summary_source === "model" && (
                        <Sparkles className="text-primary mr-1 inline h-3.5 w-3.5 align-[-2px]" />
                      )}
                      {cap.summary}
                    </p>

                    <div>
                      <SectionLabel>Takes</SectionLabel>
                      {cap.inputs.length === 0 ? (
                        <Muted>no inputs</Muted>
                      ) : (
                        <Table>
                          <TableHeader>
                            <TableRow>
                              <TableHead>Param</TableHead>
                              <TableHead>Type</TableHead>
                              <TableHead>Notes</TableHead>
                            </TableRow>
                          </TableHeader>
                          <TableBody>
                            {cap.inputs.map((p) => (
                              <TableRow key={p.name}>
                                <TableCell className="font-mono">
                                  {p.name}
                                </TableCell>
                                <TableCell className="text-muted-foreground font-mono">
                                  {p.type}
                                </TableCell>
                                <TableCell>
                                  {p.sensitive ? (
                                    <Badge
                                      variant="outline"
                                      className="text-warning border-warning/30"
                                    >
                                      sensitive
                                    </Badge>
                                  ) : (
                                    <span className="text-muted-foreground">
                                      —
                                    </span>
                                  )}
                                </TableCell>
                              </TableRow>
                            ))}
                          </TableBody>
                        </Table>
                      )}
                    </div>

                    <div>
                      <SectionLabel>Returns</SectionLabel>
                      {cap.outputs.length === 0 ? (
                        <Muted>no outputs</Muted>
                      ) : (
                        <div className="flex flex-wrap gap-1.5">
                          {cap.outputs.map((o) => (
                            <code
                              key={o.field}
                              className="bg-muted rounded px-1.5 py-0.5 text-xs"
                            >
                              {o.field}
                              <span className="text-muted-foreground">
                                :{o.shape}
                              </span>
                            </code>
                          ))}
                        </div>
                      )}
                    </div>

                    {(cap.handles.business_outcomes.length > 0 ||
                      cap.handles.recoverable.length > 0) && (
                      <>
                        <Separator />
                        <div>
                          <SectionLabel>Handles</SectionLabel>
                          <div className="flex flex-wrap gap-1.5">
                            {cap.handles.business_outcomes.map((o) => (
                              <Tooltip key={o.code}>
                                <TooltipTrigger asChild>
                                  <Badge
                                    variant="outline"
                                    className="text-warning border-warning/30 gap-1"
                                  >
                                    <Flag className="h-3 w-3" />
                                    {o.code}
                                  </Badge>
                                </TooltipTrigger>
                                <TooltipContent>
                                  {o.message ||
                                    "A legitimate non-happy outcome the caller is told about."}
                                </TooltipContent>
                              </Tooltip>
                            ))}
                            {cap.handles.recoverable.map((r) => (
                              <Badge
                                key={r}
                                variant="outline"
                                className="text-muted-foreground bg-muted gap-1"
                              >
                                <RotateCcw className="h-3 w-3" />
                                {r}
                              </Badge>
                            ))}
                          </div>
                        </div>
                      </>
                    )}

                    <Separator />
                    <p className="text-muted-foreground text-xs">
                      Recorded from run{" "}
                      <code>
                        {cap.provenance.created_from_run_id?.slice(0, 8) ?? "—"}
                      </code>
                      {cap.provenance.reviewed_by &&
                        ` · approved by ${cap.provenance.reviewed_by}`}
                    </p>
                  </div>
                </ScrollArea>
              </TabsContent>

              {/* Steps */}
              <TabsContent
                value="steps"
                className="mt-0 min-h-0 flex-1 data-[state=inactive]:hidden"
              >
                <ScrollArea className="h-full">
                  <div className="px-5 py-4">
                    <Accordion type="multiple" className="gap-0">
                      {cap.steps.map((s) => (
                        <AccordionItem key={s.i} value={String(s.i)}>
                          <AccordionTrigger className="hover:no-underline">
                            <span className="flex flex-1 items-center gap-2 pr-2 text-left">
                              <span className="bg-muted text-muted-foreground grid h-5 w-5 shrink-0 place-items-center rounded text-[11px]">
                                {s.i}
                              </span>
                              <code className="bg-muted text-foreground shrink-0 rounded px-1.5 py-0.5 text-[11px] font-semibold">
                                {s.action}
                              </code>
                              <span className="text-muted-foreground truncate text-sm font-normal">
                                {s.description}
                              </span>
                              {!s.idempotent && (
                                <Badge
                                  variant="outline"
                                  className="text-warning border-warning/30 ml-auto shrink-0 text-[10px]"
                                >
                                  mutates state
                                </Badge>
                              )}
                            </span>
                          </AccordionTrigger>
                          <AccordionContent>
                            <div className="space-y-2 pl-7 text-xs">
                              <div className="text-muted-foreground flex flex-wrap gap-x-4 gap-y-1">
                                {s.binding?.param && (
                                  <span>
                                    input{" "}
                                    <code className="text-foreground">
                                      ← {s.binding.param}
                                    </code>
                                  </span>
                                )}
                                {s.output && (
                                  <span>
                                    output{" "}
                                    <code className="text-foreground">
                                      → {s.output}
                                    </code>
                                  </span>
                                )}
                                <span>
                                  {s.idempotent ? "idempotent" : "NON-idempotent"}
                                </span>
                              </div>

                              {s.locators.length > 0 ? (
                                <div>
                                  <div className="text-muted-foreground mb-1 flex items-center gap-1 uppercase">
                                    <Crosshair className="h-3 w-3" /> finds the
                                    element by
                                  </div>
                                  <ol className="space-y-1">
                                    {s.locators.map((l, li) => (
                                      <li
                                        key={li}
                                        className={
                                          li === 0
                                            ? ""
                                            : "text-muted-foreground"
                                        }
                                      >
                                        <span className="tabular-nums">
                                          {l.rank}.
                                        </span>{" "}
                                        <span
                                          className={
                                            li === 0 ? "font-medium" : ""
                                          }
                                        >
                                          {l.kind}
                                        </span>{" "}
                                        <code className="text-muted-foreground">
                                          {JSON.stringify(l.params)}
                                        </code>
                                        {l.rationale && (
                                          <span className="text-muted-foreground/80 block pl-4">
                                            ↳ {l.rationale}
                                          </span>
                                        )}
                                      </li>
                                    ))}
                                  </ol>
                                </div>
                              ) : (
                                <p className="text-muted-foreground">
                                  {s.checkpoint
                                    ? `checkpoint: ${JSON.stringify(s.checkpoint)}`
                                    : "no element — control / assertion step"}
                                </p>
                              )}
                            </div>
                          </AccordionContent>
                        </AccordionItem>
                      ))}
                    </Accordion>
                    <p className="text-muted-foreground/80 mt-3 text-xs">
                      Replay tries each step&rsquo;s strategies top-down and takes
                      the first that resolves to one visible element — no model. A
                      match below rank 0 is logged as drift.
                    </p>
                  </div>
                </ScrollArea>
              </TabsContent>

              {/* Invoke */}
              <TabsContent
                value="invoke"
                className="mt-0 flex min-h-0 flex-1 flex-col data-[state=inactive]:hidden"
              >
                <InvokePanel cap={cap} />
              </TabsContent>
            </Tabs>

            {tab !== "invoke" && (
              <div className="bg-muted/30 flex justify-end border-t px-5 py-3">
                <Button onClick={() => setTab("invoke")}>
                  <Play className="mr-1.5 h-4 w-4" /> Invoke
                </Button>
              </div>
            )}
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-muted-foreground mb-1.5 text-xs font-medium uppercase tracking-wide">
      {children}
    </div>
  );
}

function Muted({ children }: { children: React.ReactNode }) {
  return <span className="text-muted-foreground text-xs">{children}</span>;
}

/* ---- invoke panel ------------------------------------------------- */

function InvokePanel({ cap }: { cap: Capability }) {
  const [params, setParams] = useState<Record<string, string>>({});
  const [target, setTarget] = useState("");
  const [result, setResult] = useState<ReplayResult | null>(null);

  const mut = useMutation({
    mutationFn: () => api.invoke(cap.artifact_id, cap.version, target, params),
    onSuccess: (r) => setResult(r),
    onError: (e) => toast.error(String((e as Error).message)),
  });

  return (
    <>
      <ScrollArea className="min-h-0 flex-1">
        <div className="space-y-3 px-5 py-4">
          <div className="space-y-1.5">
            <Label>Target</Label>
            <Input
              value={target}
              placeholder="https://…"
              onChange={(e) => setTarget(e.target.value)}
            />
          </div>
          {cap.inputs.map((p) => (
            <div key={p.name} className="space-y-1.5">
              <Label className="flex items-center gap-1.5">
                {p.name}
                {p.sensitive && (
                  <Badge
                    variant="outline"
                    className="text-warning border-warning/30 text-[10px]"
                  >
                    sensitive
                  </Badge>
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

          {result && (
            <div className="space-y-2 rounded-lg border p-3">
              <div className="flex flex-wrap items-center gap-2">
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
      </ScrollArea>
      <div className="bg-muted/30 border-t px-5 py-3">
        <Button
          onClick={() => mut.mutate()}
          disabled={mut.isPending || !target.trim()}
          className="w-full"
        >
          {mut.isPending ? (
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
          ) : (
            <ShieldCheck className="mr-2 h-4 w-4" />
          )}
          Run deterministic replay
        </Button>
      </div>
    </>
  );
}

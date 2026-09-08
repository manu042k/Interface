"use client";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Loader2, Play } from "lucide-react";
import { toast } from "sonner";
import { api, type Capability, type ReplayResult } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { OutcomeBadge, RiskBadge } from "@/components/badges";

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

      <div className="bg-card rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Goal</TableHead>
              <TableHead>App</TableHead>
              <TableHead>Risk</TableHead>
              <TableHead>Version</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {data?.map((c) => (
              <TableRow key={c.artifact_id}>
                <TableCell className="font-mono text-xs">{c.name}</TableCell>
                <TableCell className="text-muted-foreground max-w-sm truncate">
                  {c.goal}
                </TableCell>
                <TableCell>{c.vendor_app_id}</TableCell>
                <TableCell>
                  <RiskBadge risk={c.risk_class} />
                </TableCell>
                <TableCell>v{c.version}</TableCell>
                <TableCell className="text-right">
                  <Button size="sm" variant="outline" onClick={() => setSel(c)}>
                    <Play className="mr-1.5 h-3.5 w-3.5" /> Invoke
                  </Button>
                </TableCell>
              </TableRow>
            ))}
            {!isLoading && data?.length === 0 && (
              <TableRow>
                <TableCell
                  colSpan={6}
                  className="text-muted-foreground py-8 text-center"
                >
                  No approved capabilities yet — run a discovery and approve it.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <InvokeDialog cap={sel} onClose={() => setSel(null)} />
    </div>
  );
}

function InvokeDialog({
  cap,
  onClose,
}: {
  cap: Capability | null;
  onClose: () => void;
}) {
  const props = Object.keys(cap?.input_schema.properties ?? {});
  const [params, setParams] = useState<Record<string, string>>({});
  const [target, setTarget] = useState(
    "http://host.docker.internal:8799/search",
  );
  const [result, setResult] = useState<ReplayResult | null>(null);

  const mut = useMutation({
    mutationFn: () =>
      api.invoke(cap!.artifact_id, cap!.version, target, params),
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
            Invoke — {cap?.name}
          </DialogTitle>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label>Target</Label>
            <Input value={target} onChange={(e) => setTarget(e.target.value)} />
          </div>
          {props.map((p) => (
            <div key={p} className="space-y-1.5">
              <Label>
                {p}{" "}
                {cap?.input_schema.properties?.[p]?.["x-sensitive"] && (
                  <span className="text-warning text-xs">(sensitive)</span>
                )}
              </Label>
              <Input
                value={params[p] ?? ""}
                onChange={(e) =>
                  setParams((s) => ({ ...s, [p]: e.target.value }))
                }
              />
            </div>
          ))}
          <Button
            onClick={() => mut.mutate()}
            disabled={mut.isPending}
            className="w-full"
          >
            {mut.isPending && (
              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            )}
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
              <p className="text-muted-foreground text-xs">
                output_schema:{" "}
                <code>
                  {JSON.stringify(cap?.output_schema.properties ?? {})}
                </code>
              </p>
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}

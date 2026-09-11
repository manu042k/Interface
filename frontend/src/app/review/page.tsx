"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { toast } from "sonner";
import { api, type ArtifactSummary } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { RiskBadge } from "@/components/badges";
import { Pager, usePaged } from "@/components/pager";
import { ArtifactView } from "@/components/artifact-view";
import { PageHeader } from "@/components/page-header";
import { sentenceCase } from "@/lib/text";

export default function ReviewPage() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["drafts"],
    queryFn: () => api.artifacts("draft"),
    refetchInterval: 4000,
  });
  const [open, setOpen] = useState<ArtifactSummary | null>(null);

  const rows = data ?? [];
  const paged = usePaged(rows);

  const full = useQuery({
    queryKey: ["artifact", open?.artifact_id, open?.version],
    queryFn: () => api.artifact(open!.artifact_id, open!.version),
    enabled: !!open,
  });

  const decide = useMutation({
    mutationFn: (d: "approve" | "reject") =>
      api.promote(open!.artifact_id, open!.version, d, "operator"),
    onSuccess: (_r, d) => {
      toast.success(`Artifact ${d === "approve" ? "approved" : "rejected"}`);
      setOpen(null);
      qc.invalidateQueries({ queryKey: ["drafts"] });
      qc.invalidateQueries({ queryKey: ["capabilities"] });
    },
    onError: (e) => toast.error(String((e as Error).message)),
  });

  return (
    <div className="space-y-5">
      <PageHeader
        title="Review"
        description="Draft artifacts. Unattended replay is refused until a human approves."
      />
      <p className="text-muted-foreground -mt-3 text-xs">
        Risk is whether a step can be undone. Risky irreversible means transfer,
        wire, or close account — approving lets replay do that unattended.
      </p>

      <div className="bg-card overflow-x-auto rounded-lg border">
        <Table className="min-w-[640px]">
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Goal</TableHead>
              <TableHead>Risk</TableHead>
              <TableHead className="text-right">Steps</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {paged.pageRows.map((a) => (
              <TableRow key={a.artifact_id + a.version}>
                <TableCell className="align-top text-sm">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="font-heading font-medium whitespace-nowrap">
                      {sentenceCase(a.name)} v{a.version}
                    </span>
                    {a.record_outcome === "drift_patch" && (
                      <span className="bg-warning/12 text-warning border-warning/30 rounded border px-1.5 py-0.5 text-[10px] font-medium">
                        drift patch
                      </span>
                    )}
                    {a.duplicate_of && (
                      <span
                        className="bg-destructive/10 text-destructive border-destructive/30 max-w-[180px] truncate rounded border px-1.5 py-0.5 text-[10px] font-medium"
                        title={`possible duplicate of ${a.duplicate_of}`}
                      >
                        dup of {a.duplicate_of}
                      </span>
                    )}
                  </div>
                </TableCell>
                <TableCell className="text-muted-foreground max-w-[420px] truncate align-top">
                  {a.goal}
                </TableCell>
                <TableCell className="align-top whitespace-nowrap">
                  <RiskBadge risk={a.risk_class} />
                </TableCell>
                <TableCell className="text-muted-foreground align-top text-right tabular-nums">
                  {a.steps}
                </TableCell>
                <TableCell className="align-top text-right">
                  <Button size="sm" variant="outline" onClick={() => setOpen(a)}>
                    Review
                  </Button>
                </TableCell>
              </TableRow>
            ))}
            {rows.length === 0 && (
              <TableRow>
                <TableCell
                  colSpan={5}
                  className="text-muted-foreground py-8 text-center"
                >
                  No drafts pending.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
        <Pager
          page={paged.page}
          pageCount={paged.pageCount}
          total={paged.total}
          onPage={paged.setPage}
        />
      </div>

      <Dialog open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <DialogContent className="flex h-[85vh] w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
          <DialogHeader className="space-y-1.5 border-b px-5 py-4 text-left">
            <div className="flex flex-wrap items-center gap-x-2 gap-y-1 pr-6">
              <DialogTitle>{sentenceCase(open?.name)}</DialogTitle>
              <span className="text-muted-foreground text-xs">
                v{open?.version} · draft
              </span>
              <RiskBadge risk={open?.risk_class} />
            </div>
            <p className="text-muted-foreground text-xs">
              {open?.risk_class === "risky_irreversible"
                ? "Risky irreversible: this flow can transfer money or change an account in a way that cannot be undone. Approving lets agents replay it unattended."
                : "Safe and reversible: replay can run unattended without moving money or permanently changing an account."}
            </p>
          </DialogHeader>

          <div className="min-h-0 flex-1 space-y-4 overflow-y-auto px-5 py-4">
            {open?.goal && (
              <p className="text-sm leading-relaxed">{open.goal}</p>
            )}
            {open?.review_notes &&
              (open.record_outcome === "drift_patch" || open.duplicate_of) && (
                <p className="border-border bg-muted/40 text-muted-foreground rounded-md border border-l-2 border-l-primary/60 px-3 py-2 text-xs leading-relaxed">
                  {open.review_notes}
                </p>
              )}
            {full.isLoading && (
              <p className="text-muted-foreground text-sm">Loading artifact…</p>
            )}
            {full.data && <ArtifactView artifact={full.data as never} />}
          </div>

          <div className="bg-muted/30 flex items-center justify-between gap-3 border-t px-5 py-3">
            <span className="text-muted-foreground text-xs">
              {open?.risk_class === "risky_irreversible"
                ? "Approving authorizes unattended replay of this irreversible flow."
                : "Approving allows unattended replay."}
            </span>
            <div className="flex gap-2">
              <Button
                variant="outline"
                onClick={() => decide.mutate("reject")}
                disabled={decide.isPending}
              >
                Reject
              </Button>
              <Button
                onClick={() => decide.mutate("approve")}
                disabled={decide.isPending}
              >
                Approve
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

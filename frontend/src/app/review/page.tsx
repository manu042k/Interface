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
import { ArtifactView } from "@/components/artifact-view";

export default function ReviewPage() {
  const qc = useQueryClient();
  const { data } = useQuery({
    queryKey: ["drafts"],
    queryFn: () => api.artifacts("draft"),
    refetchInterval: 4000,
  });
  const [open, setOpen] = useState<ArtifactSummary | null>(null);

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
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">Review</h1>
        <p className="text-muted-foreground mt-1">
          Draft artifacts. Unattended replay is refused until a human approves.
        </p>
      </header>

      <div className="bg-card rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Goal</TableHead>
              <TableHead>Risk</TableHead>
              <TableHead>Steps</TableHead>
              <TableHead>Known outcomes</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {data?.map((a) => (
              <TableRow key={a.artifact_id + a.version}>
                <TableCell className="font-mono text-xs">
                  {a.name} v{a.version}
                </TableCell>
                <TableCell className="text-muted-foreground max-w-sm truncate">
                  {a.goal}
                </TableCell>
                <TableCell>
                  <RiskBadge risk={a.risk_class} />
                </TableCell>
                <TableCell>{a.steps}</TableCell>
                <TableCell className="text-muted-foreground text-xs">
                  {a.known_outcomes.join(", ") || "—"}
                </TableCell>
                <TableCell className="text-right">
                  <Button size="sm" variant="outline" onClick={() => setOpen(a)}>
                    Review
                  </Button>
                </TableCell>
              </TableRow>
            ))}
            {data?.length === 0 && (
              <TableRow>
                <TableCell
                  colSpan={6}
                  className="text-muted-foreground py-8 text-center"
                >
                  No drafts pending.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>

      <Dialog open={!!open} onOpenChange={(o) => !o && setOpen(null)}>
        <DialogContent
          showCloseButton={false}
          className="flex max-h-[88vh] w-full flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl"
        >
          <DialogHeader className="border-b px-5 py-4 text-left">
            <div className="flex flex-wrap items-center gap-2">
              <DialogTitle className="font-mono text-sm">
                {open?.name}
              </DialogTitle>
              <span className="text-muted-foreground text-xs">
                v{open?.version} · draft
              </span>
              <RiskBadge risk={open?.risk_class} />
            </div>
            {open?.goal && (
              <p className="text-muted-foreground mt-1 text-sm">{open.goal}</p>
            )}
          </DialogHeader>

          <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
            {full.isLoading && (
              <p className="text-muted-foreground text-sm">loading artifact…</p>
            )}
            {full.data && <ArtifactView artifact={full.data as never} />}
          </div>

          <div className="bg-muted/30 flex items-center justify-between gap-3 border-t px-5 py-3">
            <span className="text-muted-foreground text-xs">
              Approving allows unattended replay.
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

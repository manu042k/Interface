"use client";

import Link from "next/link";
import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { Radio } from "lucide-react";
import { api, type Capability, type RunRow } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { StatusBadge } from "@/components/badges";
import { Pager, usePaged } from "@/components/pager";
import { PageHeader } from "@/components/page-header";
import { sentenceCase } from "@/lib/text";

const LIVE = new Set(["pending", "running", "stuck"]);

function ago(ts: number) {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default function RunsPage() {
  const { data } = useQuery({
    queryKey: ["runs"],
    queryFn: api.runs,
    refetchInterval: 2500,
  });

  const { data: caps } = useQuery({
    queryKey: ["capabilities"],
    queryFn: api.capabilities,
  });

  const runs = data ?? [];
  const live = runs.filter((r) => LIVE.has(r.status));
  const goalOf = useGoalLookup(runs, caps);

  return (
    <div className="space-y-5">
      <PageHeader
        title="Runs"
        description="Discovery and replay runs, newest first."
      />

      <Tabs defaultValue="all">
        <TabsList>
          <TabsTrigger value="all">All ({runs.length})</TabsTrigger>
          <TabsTrigger value="live">
            {live.length > 0 && (
              <Radio className="text-primary h-3.5 w-3.5 animate-pulse" />
            )}
            Live ({live.length})
          </TabsTrigger>
        </TabsList>

        <TabsContent value="all" className="mt-4">
          <RunsTable rows={runs} goalOf={goalOf} empty="No runs yet." />
        </TabsContent>

        <TabsContent value="live" className="mt-4">
          <RunsTable
            rows={live}
            goalOf={goalOf}
            live
            empty="No runs in progress. Start one from New run."
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function useGoalLookup(runs: RunRow[], caps?: Capability[]) {
  return useMemo(() => {
    const byName = new Map<string, string>();
    const byArtifact = new Map<string, string>();
    for (const r of runs) {
      if (r.name && r.goal && !byName.has(r.name)) byName.set(r.name, r.goal);
    }
    for (const c of caps ?? []) {
      const g = c.goal || c.summary;
      if (!g) continue;
      if (c.name) byName.set(c.name, g);
      if (c.artifact_id) byArtifact.set(c.artifact_id, g);
    }
    return (r: RunRow) =>
      r.goal ||
      (r.artifact_id ? byArtifact.get(r.artifact_id) : undefined) ||
      (r.name ? byName.get(r.name) : undefined) ||
      null;
  }, [runs, caps]);
}

function RunsTable({
  rows,
  goalOf,
  live = false,
  empty,
}: {
  rows: RunRow[];
  goalOf: (r: RunRow) => string | null;
  live?: boolean;
  empty: string;
}) {
  const { pageRows, page, setPage, pageCount, total } = usePaged(rows);
  return (
    <div className="bg-card rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Run</TableHead>
            <TableHead>Goal</TableHead>
            <TableHead>Mode</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Steps</TableHead>
            <TableHead>Started</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {pageRows.map((r) => {
            const goal = goalOf(r);
            return (
            <TableRow key={r.run_id}>
              <TableCell className="max-w-[16rem]">
                <div className="font-heading truncate font-medium">
                  {r.name || goal ? (
                    sentenceCase(r.name || goal)
                  ) : (
                    <span className="text-muted-foreground">-</span>
                  )}
                </div>
              </TableCell>
              <TableCell className="text-muted-foreground max-w-[22rem]">
                {goal ? (
                  <span className="line-clamp-2 text-xs leading-relaxed" title={goal}>
                    {goal}
                  </span>
                ) : (
                  <span className="text-muted-foreground/60">-</span>
                )}
              </TableCell>
              <TableCell className="text-muted-foreground">{r.mode}</TableCell>
              <TableCell>
                <StatusBadge status={r.status} />
              </TableCell>
              <TableCell>{r.step_count}</TableCell>
              <TableCell className="text-muted-foreground text-xs">
                {ago(r.started_at)}
              </TableCell>
              <TableCell className="text-right">
                <Button
                  asChild
                  size="sm"
                  variant={live ? "default" : "outline"}
                >
                  <Link href={`/runs/${r.run_id}`}>
                    {live ? "View live" : "Open"}
                  </Link>
                </Button>
              </TableCell>
            </TableRow>
            );
          })}
          {rows.length === 0 && (
            <TableRow>
              <TableCell
                colSpan={7}
                className="text-muted-foreground py-8 text-center"
              >
                {empty}{" "}
                <Link href="/" className="text-primary underline">
                  New run
                </Link>
              </TableCell>
            </TableRow>
          )}
        </TableBody>
      </Table>
      <Pager
        page={page}
        pageCount={pageCount}
        total={total}
        onPage={setPage}
      />
    </div>
  );
}

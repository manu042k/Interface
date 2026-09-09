"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Radio } from "lucide-react";
import { api, type RunRow } from "@/lib/api";
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

  const runs = data ?? [];
  const live = runs.filter((r) => LIVE.has(r.status));

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">Runs</h1>
        <p className="text-muted-foreground mt-1">
          Discovery and replay runs, newest first.
        </p>
      </header>

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
          <RunsTable rows={runs} empty="No runs yet." />
        </TabsContent>

        <TabsContent value="live" className="mt-4">
          <RunsTable
            rows={live}
            live
            empty="No runs in progress. Start one from New run."
          />
        </TabsContent>
      </Tabs>
    </div>
  );
}

function RunsTable({
  rows,
  live = false,
  empty,
}: {
  rows: RunRow[];
  live?: boolean;
  empty: string;
}) {
  return (
    <div className="bg-card rounded-lg border">
      <Table>
        <TableHeader>
          <TableRow>
            <TableHead>Run</TableHead>
            <TableHead>Mode</TableHead>
            <TableHead>Status</TableHead>
            <TableHead>Steps</TableHead>
            <TableHead>Started</TableHead>
            <TableHead />
          </TableRow>
        </TableHeader>
        <TableBody>
          {rows.map((r) => (
            <TableRow key={r.run_id}>
              <TableCell className="max-w-[20rem]">
                <div className="truncate font-medium">
                  {r.name || r.goal || (
                    <span className="text-muted-foreground">-</span>
                  )}
                </div>
                {r.name && r.goal && (
                  <div className="text-muted-foreground truncate text-xs">
                    {r.goal.length > 90 ? `${r.goal.slice(0, 90)}…` : r.goal}
                  </div>
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
          ))}
          {rows.length === 0 && (
            <TableRow>
              <TableCell
                colSpan={6}
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
    </div>
  );
}

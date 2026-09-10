"use client";

import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Pager, usePaged } from "@/components/pager";
import { PageHeader } from "@/components/page-header";

export default function InterventionsPage() {
  const { data } = useQuery({
    queryKey: ["interventions"],
    queryFn: () => api.interventions("open"),
    refetchInterval: 3000,
  });
  const rows = data ?? [];
  const { pageRows, page, setPage, pageCount, total } = usePaged(rows);

  return (
    <div className="space-y-5">
      <PageHeader
        title="Interventions"
        description="Stuck runs waiting for a human. Open one to take control of its live session."
      />

      <div className="bg-card overflow-x-auto rounded-lg border">
        <Table className="min-w-[520px]">
          <TableHeader>
            <TableRow>
              <TableHead>Goal</TableHead>
              <TableHead>Why it stopped</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {pageRows.map((i) => (
              <TableRow key={i.intervention_id}>
                <TableCell
                  className="max-w-[420px] truncate align-top"
                  title={i.goal ?? undefined}
                >
                  {i.goal}
                </TableCell>
                <TableCell
                  className="text-muted-foreground max-w-[220px] truncate align-top text-xs"
                  title={i.reason}
                >
                  {i.reason}
                </TableCell>
                <TableCell className="align-top text-right">
                  <Button asChild size="sm" variant="outline">
                    <Link href={`/runs/${i.run_id}`}>Open run</Link>
                  </Button>
                </TableCell>
              </TableRow>
            ))}
            {rows.length === 0 && (
              <TableRow>
                <TableCell
                  colSpan={3}
                  className="text-muted-foreground py-8 text-center"
                >
                  Nothing stuck. Start a run with an unrecognizable goal to see
                  the handoff.
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
    </div>
  );
}

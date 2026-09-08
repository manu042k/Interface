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

export default function InterventionsPage() {
  const { data } = useQuery({
    queryKey: ["interventions"],
    queryFn: () => api.interventions("open"),
    refetchInterval: 3000,
  });

  return (
    <div className="space-y-5">
      <header>
        <h1 className="text-3xl font-semibold tracking-tight">Interventions</h1>
        <p className="text-muted-foreground mt-1">
          Stuck runs waiting for a human. Open one to take control of its live
          session.
        </p>
      </header>

      <div className="bg-card rounded-lg border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Goal</TableHead>
              <TableHead>Step</TableHead>
              <TableHead>Reason</TableHead>
              <TableHead>Tenant</TableHead>
              <TableHead />
            </TableRow>
          </TableHeader>
          <TableBody>
            {data?.map((i) => (
              <TableRow key={i.intervention_id}>
                <TableCell className="text-muted-foreground max-w-xs truncate">
                  {i.goal}
                </TableCell>
                <TableCell>{i.step_index}</TableCell>
                <TableCell className="max-w-sm truncate">{i.reason}</TableCell>
                <TableCell>{i.tenant}</TableCell>
                <TableCell className="text-right">
                  <Button asChild size="sm" variant="outline">
                    <Link href={`/runs/${i.run_id}`}>Open run</Link>
                  </Button>
                </TableCell>
              </TableRow>
            ))}
            {data?.length === 0 && (
              <TableRow>
                <TableCell
                  colSpan={5}
                  className="text-muted-foreground py-8 text-center"
                >
                  Nothing stuck. Start a run with an unrecognizable goal to see
                  the handoff.
                </TableCell>
              </TableRow>
            )}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}

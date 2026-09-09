"use client";

import { useParams } from "next/navigation";
import { Download, Printer } from "lucide-react";
import { api } from "@/lib/api";
import { Button } from "@/components/ui/button";
import { RunReport } from "@/components/run-report";

export default function ReportPage() {
  const { id } = useParams<{ id: string }>();

  return (
    <div className="space-y-5">
      <header className="no-print flex items-center justify-between">
        <h1 className="text-2xl font-semibold tracking-tight">Run report</h1>
        <div className="flex gap-2">
          <Button variant="outline" size="sm" onClick={() => window.print()}>
            <Printer className="mr-1.5 h-4 w-4" /> Print / Save PDF
          </Button>
          <Button asChild size="sm">
            <a href={api.reportMdUrl(id)} download={`report-${id}.md`}>
              <Download className="mr-1.5 h-4 w-4" /> Download .md
            </a>
          </Button>
        </div>
      </header>

      <RunReport id={id} />
    </div>
  );
}

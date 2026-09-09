"use client";

import { useEffect, useState } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export const PAGE_SIZE = 12;

/** Client-side pagination for a list. Clamps the page when the list shrinks. */
export function usePaged<T>(rows: T[], size: number = PAGE_SIZE) {
  const [page, setPage] = useState(0);
  const pageCount = Math.max(1, Math.ceil(rows.length / size));

  useEffect(() => {
    if (page > pageCount - 1) setPage(pageCount - 1);
  }, [page, pageCount]);

  const start = page * size;
  return {
    pageRows: rows.slice(start, start + size),
    page,
    setPage,
    pageCount,
    total: rows.length,
  };
}

export function Pager({
  page,
  pageCount,
  total,
  onPage,
  className,
}: {
  page: number;
  pageCount: number;
  total: number;
  onPage: (p: number) => void;
  className?: string;
}) {
  if (pageCount <= 1) return null;
  return (
    <div
      className={cn(
        "text-muted-foreground border-border/60 flex items-center justify-between border-t px-3 py-2 text-xs",
        className,
      )}
    >
      <span>{total} total</span>
      <div className="flex items-center gap-2">
        <Button
          variant="ghost"
          size="icon-sm"
          disabled={page <= 0}
          onClick={() => onPage(page - 1)}
        >
          <ChevronLeft className="h-4 w-4" />
        </Button>
        <span className="tabular-nums">
          {page + 1} / {pageCount}
        </span>
        <Button
          variant="ghost"
          size="icon-sm"
          disabled={page >= pageCount - 1}
          onClick={() => onPage(page + 1)}
        >
          <ChevronRight className="h-4 w-4" />
        </Button>
      </div>
    </div>
  );
}

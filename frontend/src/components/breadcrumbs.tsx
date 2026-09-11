"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { ChevronRight } from "lucide-react";

const LABELS: Record<string, string> = {
  runs: "Runs",
  capabilities: "Capabilities",
  "prefill-runs": "Prefill runs",
  review: "Review",
  interventions: "Interventions",
  report: "Report",
};

type Crumb = { label: string; href: string };

function crumbsFor(pathname: string): Crumb[] {
  const parts = pathname.split("/").filter(Boolean);
  if (parts.length === 0) return [{ label: "New run", href: "/" }];

  let href = "";
  return parts.map((seg) => {
    href += `/${seg}`;
    const label =
      LABELS[seg] ??
      (seg.length > 14 ? `${seg.slice(0, 8)}…${seg.slice(-4)}` : seg);
    return { label, href };
  });
}

export function Breadcrumbs() {
  const pathname = usePathname();
  const crumbs = crumbsFor(pathname);

  return (
    <nav
      aria-label="Breadcrumb"
      className="flex min-w-0 items-center gap-1.5 text-sm"
    >
      <Link
        href="/"
        className="font-heading text-muted-foreground hover:text-foreground shrink-0 font-medium"
      >
        Replay
      </Link>
      {crumbs.map((c, i) => {
        const last = i === crumbs.length - 1;
        return (
          <span key={c.href} className="flex min-w-0 items-center gap-1.5">
            <ChevronRight className="text-muted-foreground/40 h-3.5 w-3.5 shrink-0" />
            {last ? (
              <span className="font-heading text-foreground truncate font-semibold">
                {c.label}
              </span>
            ) : (
              <Link
                href={c.href}
                className="text-muted-foreground hover:text-foreground shrink-0"
              >
                {c.label}
              </Link>
            )}
          </span>
        );
      })}
    </nav>
  );
}

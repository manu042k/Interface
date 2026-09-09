"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  Sparkles,
  ListChecks,
  ClipboardCheck,
  LifeBuoy,
  Activity,
  Waypoints,
} from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";
import { Breadcrumbs } from "@/components/breadcrumbs";

const NAV = [
  { href: "/", label: "New run", icon: Sparkles, exact: true },
  { href: "/runs", label: "Runs", icon: Activity, exact: true },
  { href: "/capabilities", label: "Capabilities", icon: ListChecks },
  { href: "/review", label: "Review", icon: ClipboardCheck },
  { href: "/interventions", label: "Interventions", icon: LifeBuoy },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const { data: active } = useQuery({
    queryKey: ["active-run"],
    queryFn: api.activeRun,
    refetchInterval: 3000,
  });

  return (
    <div className="flex h-screen overflow-hidden">
      <aside className="no-print bg-sidebar border-sidebar-border hidden w-60 shrink-0 flex-col border-r px-4 py-6 md:flex">
        <Link href="/" className="mb-7 flex items-center gap-2 px-2">
          <span className="bg-primary text-primary-foreground grid h-7 w-7 place-items-center rounded-md">
            <Waypoints className="h-4 w-4" strokeWidth={2.5} />
          </span>
          <span className="text-[15px] font-semibold tracking-tight">Replay</span>
        </Link>

        <nav className="flex flex-col gap-1">
          {NAV.map(({ href, label, icon: Icon, exact }) => {
            const activeNav = exact ? path === href : path.startsWith(href);
            const showLive = href === "/runs" && !!active;
            return (
              <Link
                key={href}
                href={showLive && active ? `/runs/${active.run_id}` : href}
                className={cn(
                  "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
                  activeNav
                    ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                    : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4" />
                {label}
                {showLive && (
                  <span className="relative ml-auto flex h-2 w-2">
                    <span className="bg-primary absolute inline-flex h-full w-full animate-ping rounded-full opacity-75" />
                    <span className="bg-primary relative inline-flex h-2 w-2 rounded-full" />
                  </span>
                )}
              </Link>
            );
          })}
        </nav>
      </aside>

      <main className="flex min-w-0 flex-1 flex-col overflow-hidden">
        <header className="no-print bg-background/85 sticky top-0 z-10 flex h-12 shrink-0 items-center gap-3 border-b px-6 backdrop-blur md:px-10">
          <Breadcrumbs />
          {path !== "/" && (
            <Link
              href="/"
              className="border-input hover:border-primary hover:text-foreground text-muted-foreground ml-auto flex shrink-0 items-center gap-1.5 rounded-md border px-2.5 py-1 text-xs font-medium transition-colors"
            >
              <Sparkles className="h-3.5 w-3.5" />
              New run
            </Link>
          )}
        </header>
        <div className="flex-1 overflow-y-auto px-6 py-8 md:px-10">
          <div className="mx-auto h-full max-w-6xl">{children}</div>
        </div>
      </main>
    </div>
  );
}

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
  Radio,
} from "lucide-react";
import { api } from "@/lib/api";
import { cn } from "@/lib/utils";

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
          <span className="bg-primary text-primary-foreground grid h-7 w-7 place-items-center rounded-md text-sm font-bold">
            C
          </span>
          <span className="text-[15px] font-semibold tracking-tight">
            Computer-Use Automation
          </span>
        </Link>

        <nav className="flex flex-col gap-1">
          {NAV.map(({ href, label, icon: Icon, exact }) => {
            const activeNav = exact ? path === href : path.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
                  activeNav
                    ? "bg-sidebar-accent text-sidebar-accent-foreground font-medium"
                    : "text-muted-foreground hover:bg-sidebar-accent/60 hover:text-foreground",
                )}
              >
                <Icon className="h-4 w-4" />
                {label}
              </Link>
            );
          })}
        </nav>

        {active && (
          <Link
            href={`/runs/${active.run_id}`}
            className="border-primary/40 bg-primary/8 hover:bg-primary/12 mt-4 flex items-start gap-2 rounded-lg border px-3 py-2.5 text-xs transition-colors"
          >
            <Radio className="text-primary mt-0.5 h-3.5 w-3.5 shrink-0 animate-pulse" />
            <span className="min-w-0">
              <span className="text-primary block font-semibold">
                Live run · {active.status}
              </span>
              <span className="text-muted-foreground line-clamp-2">
                {active.goal}
              </span>
            </span>
          </Link>
        )}

        <p className="text-muted-foreground mt-auto px-2 pt-4 text-xs leading-relaxed">
          The model discovers. The artifact becomes a capability. Deterministic
          replay is how the agent invokes it.
        </p>
      </aside>

      <main className="min-w-0 flex-1 overflow-y-auto px-6 py-8 md:px-10">
        <div className="mx-auto h-full max-w-6xl">{children}</div>
      </main>
    </div>
  );
}

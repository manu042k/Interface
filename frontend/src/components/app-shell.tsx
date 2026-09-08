"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Sparkles, ListChecks, ClipboardCheck, LifeBuoy } from "lucide-react";
import { cn } from "@/lib/utils";

const NAV = [
  { href: "/", label: "New run", icon: Sparkles, exact: true },
  { href: "/capabilities", label: "Capabilities", icon: ListChecks },
  { href: "/review", label: "Review", icon: ClipboardCheck },
  { href: "/interventions", label: "Interventions", icon: LifeBuoy },
];

export function AppShell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  return (
    <div className="flex min-h-screen">
      <aside className="no-print bg-sidebar border-sidebar-border hidden w-60 shrink-0 flex-col border-r px-4 py-6 md:flex">
        <Link href="/" className="mb-8 flex items-center gap-2 px-2">
          <span className="bg-primary text-primary-foreground grid h-7 w-7 place-items-center rounded-md text-sm font-bold">
            C
          </span>
          <span className="text-[15px] font-semibold tracking-tight">
            Computer-Use Automation
          </span>
        </Link>
        <nav className="flex flex-col gap-1">
          {NAV.map(({ href, label, icon: Icon, exact }) => {
            const active = exact ? path === href : path.startsWith(href);
            return (
              <Link
                key={href}
                href={href}
                className={cn(
                  "flex items-center gap-2.5 rounded-md px-3 py-2 text-sm transition-colors",
                  active
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
        <p className="text-muted-foreground mt-auto px-2 text-xs leading-relaxed">
          The model discovers. The artifact becomes a capability. Deterministic
          replay is how the agent invokes it.
        </p>
      </aside>
      <main className="min-w-0 flex-1 px-6 py-8 md:px-10">
        <div className="mx-auto max-w-6xl">{children}</div>
      </main>
    </div>
  );
}

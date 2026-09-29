"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

export function Providers({ children }: { children: React.ReactNode }) {
  // Lazy initializer (`useState(() => ...)`) so the QueryClient is created
  // exactly once per mount, not re-instantiated (losing all cached queries)
  // on every re-render.
  const [client] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          // Live run/capability pages already poll on their own interval;
          // an extra refetch on window focus is redundant noise here.
          // retry: 1 (not the default 3) so a real backend error surfaces
          // quickly instead of being masked as transient flakiness.
          queries: { refetchOnWindowFocus: false, retry: 1 },
        },
      }),
  );
  return (
    <QueryClientProvider client={client}>
      <TooltipProvider delayDuration={200}>{children}</TooltipProvider>
      <Toaster richColors position="top-right" />
    </QueryClientProvider>
  );
}

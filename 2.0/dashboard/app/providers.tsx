"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";

// All dashboard data is short-lived — auto-refresh every 5s, treat data as
// stale after 2s so manual refetches (e.g. tab focus) trip the network too.
export function Providers({ children }: { children: React.ReactNode }) {
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            refetchInterval: 5000,
            staleTime: 2000,
            // We render error states ourselves — don't retry transient
            // 5xx/CORS errors before we get a chance to show them.
            retry: 1,
          },
        },
      }),
  );

  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}

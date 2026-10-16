"use client";

import { useState } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { DEMO_MODE } from "@/lib/demo";

export function QueryProvider({ children }: { children: React.ReactNode }) {
  // useState ensures a single QueryClient per browser session, not per render.
  const [queryClient] = useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            retry: 1,
            // Demo mode: Talkse writes calls straight into the database, so every
            // page re-polls every 5s and new leads/deals/tasks appear without a reload
            refetchOnWindowFocus: DEMO_MODE,
            refetchInterval: DEMO_MODE ? 5_000 : false,
          },
        },
      })
  );

  return <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>;
}

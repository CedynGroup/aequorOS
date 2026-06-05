import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, type RenderOptions } from "@testing-library/react";
import type { ReactElement } from "react";

type QueryProviderChildren = Parameters<typeof QueryClientProvider>[0]["children"];

export function renderWithQuery(
  ui: ReactElement,
  options?: Omit<RenderOptions, "wrapper">,
) {
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: 0 },
      mutations: { retry: false },
    },
  });

  return {
    queryClient,
    ...render(ui, {
      wrapper: ({ children }: { children: unknown }) => (
        <QueryClientProvider client={queryClient}>
          {children as QueryProviderChildren}
        </QueryClientProvider>
      ),
      ...options,
    }),
  };
}

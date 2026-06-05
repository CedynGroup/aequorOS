import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  Outlet,
  RouterProvider,
  createRootRoute,
  createRoute,
  createRouter,
  redirect,
} from "@tanstack/react-router";
import { lazy, StrictMode, Suspense } from "react";
import { createRoot } from "react-dom/client";
import { Toaster } from "sonner";

import { RiskConsoleRoute } from "./routes/risk-console";
import { parseSearchState } from "./routes/search";
import "./styles.css";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      retry: 1,
      staleTime: 20_000,
    },
  },
});

const RouterDevtools = import.meta.env.DEV
  ? lazy(() =>
      import("@tanstack/react-router-devtools").then((module) => ({
        default: module.TanStackRouterDevtools,
      })),
    )
  : null;
const QueryDevtools = import.meta.env.DEV
  ? lazy(() =>
      import("@tanstack/react-query-devtools").then((module) => ({
        default: module.ReactQueryDevtools,
      })),
    )
  : null;

const rootRoute = createRootRoute({
  component: () => (
    <>
      <Outlet />
      <Toaster richColors position="bottom-right" />
      {RouterDevtools && QueryDevtools ? (
        <Suspense fallback={null}>
          <RouterDevtools position="bottom-left" />
          <QueryDevtools initialIsOpen={false} />
        </Suspense>
      ) : null}
    </>
  ),
});

const indexRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/",
  beforeLoad: () => {
    throw redirect({ to: "/cases" });
  },
});

const casesRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/cases",
  validateSearch: parseSearchState,
  component: RiskConsoleRoute,
});

const caseRoute = createRoute({
  getParentRoute: () => rootRoute,
  path: "/cases/$caseId",
  validateSearch: parseSearchState,
  component: RiskConsoleRoute,
});

const router = createRouter({
  routeTree: rootRoute.addChildren([indexRoute, casesRoute, caseRoute]),
});

declare module "@tanstack/react-router" {
  interface Register {
    router: typeof router;
  }
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);

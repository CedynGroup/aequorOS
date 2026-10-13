import React, { type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { QueryAuthorityScopeProvider } from "../lib/api/useQueryScope";
import { queryAuthorityScope } from "../lib/api/queryPolicy";
import {
  BiQueryFromJSON,
  BiQueryResultFromJSON,
} from "@aequoros/risk-service-api";
import { ApiError } from "../lib/api/client";

export const queryWire = {
  measures: ["loans.balance_rc"],
  dimensions: ["branch.code"],
  time: { as_of: "2026-09-30" },
};
export const query = BiQueryFromJSON(queryWire);
export const catalogueWire = {
  version: "v1",
  hierarchies: [],
  measures: [
    {
      id: "loans.balance_rc",
      label: "Loan balance",
      description: "Loan balances",
      module: "credit",
      sensitivity: "aggregated",
      measure_kind: "portfolio",
      aggregation: "sum",
      time_behaviour: "stock",
      value_type: "amount",
      grain: "portfolio",
      allowed_dimensions: ["branch.code"],
      favourable_direction: "neutral",
      certified: false,
    },
  ],
  dimensions: [
    {
      id: "branch.code",
      label: "Branch",
      description: "The institution's branches",
      module: "credit",
      sensitivity: "aggregated",
      value_type: "text",
      values: [
        { value: "B001", label: "Branch one" },
        { value: "B002", label: "Branch two" },
      ],
    },
  ],
};
export const columns = [
  {
    id: "branch",
    member_id: "branch.code",
    label: "Branch",
    kind: "dimension",
    format: "text",
  },
  {
    id: "balance",
    member_id: "loans.balance_rc",
    label: "Loan balance",
    kind: "measure",
    format: "amount",
  },
];
export function result(rows: unknown[][] = [], truncated = false) {
  return BiQueryResultFromJSON({
    columns,
    rows,
    truncated,
    as_of: "2026-09-30",
    catalogue_version: "v1",
    dimension_count: 1,
    used_aggregate: true,
  });
}
export function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
export function refusal(
  code: string,
  message = "The platform cannot serve this request.",
) {
  return new ApiError({
    status: 403,
    errorCode: code,
    code: "http_error",
    message,
    details: { members: ["SECRET MEMBER"] },
  });
}
export function providers() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, refetchOnWindowFocus: false },
      mutations: { retry: false },
    },
  });
  function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={client}>
        <QueryAuthorityScopeProvider
          scope={queryAuthorityScope("OR-test", "reader@example.com", 1)}
        >
          {children}
        </QueryAuthorityScopeProvider>
      </QueryClientProvider>
    );
  }
  return { client, wrapper: Wrapper };
}

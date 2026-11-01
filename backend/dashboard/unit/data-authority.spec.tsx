// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  render,
  renderHook,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import fc from "fast-check";
import {
  ReconciliationApi,
  type EffectiveCapabilityRead,
  type ReconciliationExceptionRead,
} from "@aequoros/risk-service-api";
import { hasDataCapability } from "../lib/modules";
import { useDataPermission } from "../lib/api/dataAccess";
import { ingestionApi, useIngestionBatches } from "../lib/api/ingestion";
import { queryAuthorityScope } from "../lib/api/queryPolicy";
import ReconciliationPanel from "../components/data-engine/ReconciliationPanel";
import UploadPanel from "../components/data-engine/UploadPanel";

const state = vi.hoisted(() => ({
  capabilities: [] as EffectiveCapabilityRead[],
  bank: "BK-SAMP0001",
  actor: "maker",
  version: 1,
  loading: false,
  impersonating: false,
}));
vi.mock("@/components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({
    profile: { userId: state.actor },
    isLoading: state.loading,
    effectiveAuthority: {
      institutionCapabilities: [
        {
          institutionId: state.bank,
          capabilities: state.capabilities,
        },
      ],
    },
  }),
}));
vi.mock("@/components/shell/BankContext", () => ({
  useBankContext: () => ({ bank: { id: state.bank } }),
}));
vi.mock("@/components/impersonation/useImpersonation", () => ({
  useImpersonation: () => ({ impersonating: state.impersonating }),
}));
vi.mock("@/lib/api/useQueryScope", () => ({
  useQueryAuthorityScope: () =>
    queryAuthorityScope("OR-DEM00001", state.actor, state.version),
}));
vi.mock("@/lib/api/client", () => ({
  configuration: undefined,
  apiCall: (call: () => Promise<unknown>) => call(),
  isApiError: () => false,
}));

function capability(
  permission: EffectiveCapabilityRead["permission"],
  sensitivity: EffectiveCapabilityRead["sensitivity"] = "restricted",
): EffectiveCapabilityRead {
  return {
    module: "data",
    permission,
    sensitivity,
    dataScope: { kind: "all", branches: [], regions: [] },
    requiresContextualAuthorization: permission === "approve",
  };
}
let client: QueryClient;
function Wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
beforeEach(() => {
  state.capabilities = [];
  state.actor = "maker";
  state.bank = "BK-SAMP0001";
  state.version = 1;
  state.loading = state.impersonating = false;
  client = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
});
afterEach(() => {
  cleanup();
  client.clear();
  vi.restoreAllMocks();
});

it("requires one exact DATA capability, never a union of unrelated projections", () => {
  fc.assert(
    fc.property(
      fc.array(
        fc.record({
          module: fc.constantFrom("data" as const, "credit" as const),
          sensitivity: fc.constantFrom(
            "aggregated" as const,
            "confidential" as const,
            "restricted" as const,
          ),
          permission: fc.constantFrom(
            "view" as const,
            "run" as const,
            "validate" as const,
          ),
          dataScope: fc.constantFrom<EffectiveCapabilityRead["dataScope"]>(
            { kind: "all", branches: [], regions: [] },
            { kind: "branch", branches: ["fixture"], regions: [] },
          ),
          requiresContextualAuthorization: fc.boolean(),
        }),
      ),
      (capabilities) => {
        expect(hasDataCapability(capabilities, "restricted", "run")).toBe(
          capabilities.some(
            (c) =>
              c.module === "data" &&
              c.sensitivity === "restricted" &&
              c.permission === "run" &&
              c.dataScope.kind === "all" &&
              !c.requiresContextualAuthorization,
          ),
        );
      },
    ),
  );
});

it("keeps RUN and VALIDATE separate and refuses loading, foreign banks and impersonation", () => {
  state.capabilities = [capability("run")];
  const { result, rerender } = renderHook(() => ({
    run: useDataPermission("BK-SAMP0001", "run", "restricted"),
    validate: useDataPermission("BK-SAMP0001", "validate", "restricted"),
    foreign: useDataPermission("BK-OTHER001", "run", "restricted"),
  }));
  expect(result.current).toEqual({
    run: true,
    validate: false,
    foreign: false,
  });
  state.loading = true;
  rerender();
  expect(result.current.run).toBe(false);
  state.loading = false;
  state.impersonating = true;
  rerender();
  expect(result.current.run).toBe(false);
  state.impersonating = false;
  state.capabilities = [capability("validate")];
  rerender();
  expect(result.current).toEqual({
    run: false,
    validate: true,
    foreign: false,
  });
});

it("does not fetch confidential metadata without VIEW and partitions refreshed authority", async () => {
  const list = vi
    .spyOn(ingestionApi, "listIngestionBatches")
    .mockResolvedValue({ bankId: state.bank, batches: [] });
  state.capabilities = [capability("view", "aggregated")];
  const { result, rerender } = renderHook(
    () => useIngestionBatches(state.bank),
    { wrapper: Wrapper },
  );
  expect(result.current.fetchStatus).toBe("idle");
  expect(list).not.toHaveBeenCalled();
  state.capabilities = [capability("view", "confidential")];
  state.version += 1;
  rerender();
  await waitFor(() => expect(result.current.isSuccess).toBe(true));
  expect(list).toHaveBeenCalledTimes(1);
  state.version += 1;
  rerender();
  await waitFor(() => expect(list).toHaveBeenCalledTimes(2));
});

it("shows a grant notice, not an upload action, when either CREATE or RUN is missing", () => {
  state.capabilities = [capability("create")];
  const { rerender } = render(
    <Wrapper>
      <UploadPanel />
    </Wrapper>,
  );
  expect(screen.getByRole("status")).toHaveTextContent(/create and run/i);
  expect(
    screen.queryByRole("button", { name: /ingest/i }),
  ).not.toBeInTheDocument();
  state.capabilities = [capability("run")];
  rerender(
    <Wrapper>
      <UploadPanel />
    </Wrapper>,
  );
  expect(screen.getByRole("status")).toHaveTextContent(/create and run/i);
});

it("offers a pending proposal only to a different signed-in checker, never a display label", async () => {
  const row: ReconciliationExceptionRead = {
    id: "proposal",
    bankId: state.bank,
    status: "pending",
    control: "balance_sheet_identity",
    requestedBy: "maker",
    requestedAt: new Date("2026-01-01"),
    reason: "Fixture feed gap",
    maxGapFraction: "0.01",
    effectiveFrom: new Date("2026-01-01"),
    effectiveTo: null,
    approvedBy: null,
    approvedByUserId: null,
    approvalTimestamp: null,
    revokedAt: null,
    revokedBy: null,
  };
  vi.spyOn(
    ReconciliationApi.prototype,
    "listReconciliationExceptions",
  ).mockResolvedValue({
    bankId: state.bank,
    control: row.control,
    asOf: new Date("2026-01-01"),
    activeExceptionId: null,
    exceptions: [row],
  });
  const approve = vi
    .spyOn(ReconciliationApi.prototype, "approveReconciliationException")
    .mockResolvedValue({
      ...row,
      status: "approved",
      approvedByUserId: "checker",
      approvedBy: "CRO",
    });
  state.capabilities = [
    capability("view", "confidential"),
    capability("approve", "confidential"),
  ];
  const { rerender } = render(
    <Wrapper>
      <ReconciliationPanel />
    </Wrapper>,
  );
  await screen.findByText("Fixture feed gap");
  const user = userEvent.setup();
  await user.type(
    screen.getByLabelText("Your approval or revocation label"),
    "CRO",
  );
  expect(
    screen.getByRole("button", { name: "Approve proposal" }),
  ).toBeDisabled();
  expect(approve).not.toHaveBeenCalled();
  state.actor = "checker";
  rerender(
    <Wrapper>
      <ReconciliationPanel />
    </Wrapper>,
  );
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Approve proposal" }),
    ).toBeEnabled(),
  );
  await user.click(screen.getByRole("button", { name: "Approve proposal" }));
  await waitFor(() =>
    expect(approve).toHaveBeenCalledWith({
      bankId: state.bank,
      exceptionId: "proposal",
      reconciliationExceptionApprove: { approvedBy: "CRO" },
    }),
  );
});

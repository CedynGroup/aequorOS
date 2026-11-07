// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type {
  BehavioralApplyProduct,
  BehavioralModelRead,
  BehavioralProposalAcknowledgement,
  BehavioralProposalRead,
  EffectiveCapabilityRead,
} from "@aequoros/risk-service-api";
import BehavioralModelPage from "../../components/behavioral/BehavioralModelPage";

const BANK = "BK-SAMP0001";
const PROPOSAL_ID = "11111111-1111-4111-8111-111111111111";
const OTHER_PROPOSAL_ID = "22222222-2222-4222-8222-222222222222";
const PROPOSER = "proposer";
const REVIEWER = "reviewer";

type Fixture = {
  actor: string;
  loading: boolean;
  error: Error | null;
  inspecting: boolean;
  bankCapabilities: EffectiveCapabilityRead[];
  otherBankCapabilities: EffectiveCapabilityRead[];
  proposals: BehavioralProposalRead[];
  proposalReads: number;
  applyCalls: BehavioralApplyProduct[][];
  reviewCalls: string[];
  reviewPending: boolean;
  reviewVariables: string | undefined;
  reviewError: Error | null;
};

const fixture = vi.hoisted((): Fixture => ({
  actor: "reviewer",
  loading: false,
  error: null,
  inspecting: false,
  bankCapabilities: [],
  otherBankCapabilities: [],
  proposals: [],
  proposalReads: 0,
  applyCalls: [],
  reviewCalls: [],
  reviewPending: false,
  reviewVariables: undefined,
  reviewError: null,
}));

vi.mock("../../components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: { id: BANK },
    period: null,
    moduleScope: { behavioralAggregatedView: true, behavioralRun: false },
  }),
}));
vi.mock("../../components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({
    profile: { userId: fixture.actor },
    effectiveAuthority: {
      institutionCapabilities: [
        { institutionId: BANK, capabilities: fixture.bankCapabilities },
        {
          institutionId: "BK-OTHER000",
          capabilities: fixture.otherBankCapabilities,
        },
      ],
      organizationCapabilities: [],
    },
    isLoading: fixture.loading,
    error: fixture.error,
  }),
}));
vi.mock("../../components/impersonation/useImpersonation", () => ({
  useImpersonation: () => ({ impersonating: fixture.inspecting }),
}));
vi.mock("../../lib/api/hooks", () => ({
  isServiceUnavailableError: () => false,
  useBehavioralModel: () => ({ data: model, error: null, isLoading: false }),
  useTrainBehavioralModel: () => ({
    mutate: vi.fn(),
    isPending: false,
  }),
  useApplyBehavioralModel: () => ({
    mutate: (
      products: BehavioralApplyProduct[],
      options: {
        onSuccess: (result: BehavioralProposalAcknowledgement) => void;
      },
    ) => {
      fixture.applyCalls.push(products);
      options.onSuccess({ proposalId: PROPOSAL_ID, status: "pending" });
    },
    isPending: false,
    error: null,
  }),
  useBehavioralModelProposals: (bankId: string | undefined) => {
    if (bankId) fixture.proposalReads += 1;
    return {
      data: bankId ? { proposals: fixture.proposals } : undefined,
      error: null,
      isLoading: false,
    };
  },
  useReviewBehavioralModelProposal: () => ({
    mutate: (proposalId: string) => fixture.reviewCalls.push(proposalId),
    isPending: fixture.reviewPending,
    variables: fixture.reviewVariables,
    error: fixture.reviewError,
  }),
}));

const model: BehavioralModelRead = {
  modelId: "nmd-duration",
  modelVersion: "model-v7",
  method: "baseline",
  asOfDate: new Date("2026-09-30T00:00:00Z"),
  accuracy: {
    method: "baseline",
    sampleCount: 12,
    monthCoverage: 12,
    cvMae: null,
    cvRmse: null,
  },
  products: [
    {
      productCode: "CURRENT",
      assumptionType: "nmd_duration",
      value: 18,
      unit: "months",
      confidence: 0.8,
      method: "baseline",
    },
  ],
};

function capability(
  permission: EffectiveCapabilityRead["permission"],
  overrides: Partial<EffectiveCapabilityRead> = {},
): EffectiveCapabilityRead {
  return {
    module: "beh",
    sensitivity: "restricted",
    permission,
    requiresContextualAuthorization: permission === "approve",
    dataScope: { kind: "all", branches: [], regions: [] },
    ...overrides,
  };
}

function pendingProposal(
  overrides: Partial<BehavioralProposalRead> = {},
): BehavioralProposalRead {
  return {
    proposalId: PROPOSAL_ID,
    bankId: BANK,
    model: "nmd-duration",
    modelVersion: "model-v7",
    asOfDate: new Date("2026-09-30T00:00:00Z"),
    values: [
      { productCode: "CURRENT", value: 18, unit: "months", confidence: 0.8 },
    ],
    status: "pending",
    proposedByUserId: PROPOSER,
    proposedAt: new Date("2026-11-07T09:00:00Z"),
    ...overrides,
  };
}

function mountPage() {
  return render(
    <BehavioralModelPage
      slug="nmd-duration"
      config={{
        title: "NMD Duration",
        valueLabel: "Duration",
        format: (value) => `${value} mo`,
        avgValue: (value) => value,
        avgSuffix: "mo",
        avgDecimals: 0,
      }}
    />,
  );
}

beforeEach(() => {
  fixture.actor = REVIEWER;
  fixture.loading = false;
  fixture.error = null;
  fixture.inspecting = false;
  fixture.bankCapabilities = [
    capability("view", { sensitivity: "aggregated" }),
  ];
  fixture.otherBankCapabilities = [];
  fixture.proposals = [];
  fixture.proposalReads = 0;
  fixture.applyCalls = [];
  fixture.reviewCalls = [];
  fixture.reviewPending = false;
  fixture.reviewVariables = undefined;
  fixture.reviewError = null;
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("keeps propose disabled and explained for a read-only officer without loading the restricted queue", async () => {
  mountPage();
  const propose = screen.getByRole("button", { name: "Propose 1 estimate" });
  expect(propose).toBeDisabled();
  propose.parentElement?.focus();
  await waitFor(() =>
    expect(propose).toHaveAccessibleDescription(
      /Requires Behavioral Models configure access with Restricted data for this institution/i,
    ),
  );
  expect(
    screen.getByText(
      /Restricted data view access is required to load proposal evidence/i,
    ),
  ).toBeVisible();
  expect(fixture.proposalReads).toBe(0);
  await userEvent.setup().click(propose);
  expect(fixture.applyCalls).toEqual([]);
});

it("stores only the proposal acknowledgement and does not read protected evidence for a configure-only proposer", async () => {
  fixture.bankCapabilities.push(capability("configure"));
  mountPage();
  const propose = screen.getByRole("button", { name: "Propose 1 estimate" });
  expect(propose).toBeEnabled();
  await userEvent.setup().click(propose);
  expect(fixture.applyCalls).toEqual([
    [{ productCode: "CURRENT", value: 18, unit: "months", confidence: 0.8 }],
  ]);
  expect(
    await screen.findByText(
      (_, element) =>
        element?.tagName === "P" &&
        /Proposal 11111111… is pending independent review/i.test(
          element.textContent ?? "",
        ),
    ),
  ).toBeVisible();
  expect(screen.queryByText(/Applied 1 estimate/i)).toBeNull();
  expect(fixture.proposalReads).toBe(0);
});

it("uses restricted view to show immutable proposal evidence but denies review without approve", async () => {
  fixture.bankCapabilities.push(capability("view"));
  fixture.proposals = [pendingProposal()];
  mountPage();
  const queue = screen
    .getByRole("heading", {
      name: "Independent proposal review",
    })
    .closest("section");
  if (!queue) throw new Error("proposal queue did not render");
  expect(await within(queue).findByText("model-v7")).toBeVisible();
  expect(
    within(queue).getByText(/CURRENT: 18 months · 80% confidence/i),
  ).toBeVisible();
  expect(within(queue).getByText(PROPOSER)).toBeVisible();
  const review = screen.getByRole("button", { name: "Review and accept" });
  expect(review).toBeDisabled();
  review.parentElement?.focus();
  await waitFor(() =>
    expect(review).toHaveAccessibleDescription(
      /Requires Behavioral Models approve access with Restricted data for this institution/i,
    ),
  );
  await userEvent.setup().click(review);
  expect(fixture.reviewCalls).toEqual([]);
});

it("does not load evidence or offer review to an approver without restricted view", () => {
  fixture.bankCapabilities.push(capability("approve"));
  fixture.proposals = [pendingProposal()];
  mountPage();
  expect(
    screen.queryByRole("button", { name: "Review and accept" }),
  ).toBeNull();
  expect(fixture.proposalReads).toBe(0);
});

it("allows an independent restricted approver to review the exact queued proposal", async () => {
  fixture.bankCapabilities.push(capability("view"), capability("approve"));
  fixture.proposals = [pendingProposal()];
  mountPage();
  const review = await screen.findByRole("button", {
    name: "Review and accept",
  });
  await waitFor(() => expect(review).toBeEnabled());
  await userEvent.setup().click(review);
  expect(fixture.reviewCalls).toEqual([PROPOSAL_ID]);
});

it("scopes in-flight review progress and errors to the selected proposal", () => {
  fixture.bankCapabilities.push(capability("view"), capability("approve"));
  fixture.proposals = [
    pendingProposal(),
    pendingProposal({ proposalId: OTHER_PROPOSAL_ID }),
  ];
  fixture.reviewPending = true;
  fixture.reviewVariables = OTHER_PROPOSAL_ID;
  fixture.reviewError = new Error("synthetic review failure");
  mountPage();

  const reviews = screen.getAllByRole("button", { name: "Review and accept" });
  const idleCard = reviews[0]?.closest("article");
  const pendingCard = reviews[1]?.closest("article");
  if (!idleCard || !pendingCard) throw new Error("proposal cards did not render");
  expect(reviews[0]).toBeEnabled();
  expect(reviews[1]).toBeDisabled();
  expect(within(idleCard).queryByText(/Could not review/i)).toBeNull();
  expect(
    within(pendingCard).getByText(/Could not review — synthetic review failure/i),
  ).toBeVisible();
});

it("explains the maker-checker refusal when the approver is also the proposer", async () => {
  fixture.actor = PROPOSER;
  fixture.bankCapabilities.push(capability("view"), capability("approve"));
  fixture.proposals = [pendingProposal()];
  mountPage();
  const review = await screen.findByRole("button", {
    name: "Review and accept",
  });
  expect(review).toBeDisabled();
  review.parentElement?.focus();
  await waitFor(() =>
    expect(review).toHaveAccessibleDescription(
      /A different authorized reviewer is required; the proposer cannot review their own proposal/i,
    ),
  );
  await userEvent.setup().click(review);
  expect(fixture.reviewCalls).toEqual([]);
});

it("does not borrow another institution's configure capability", () => {
  fixture.otherBankCapabilities = [capability("configure")];
  mountPage();
  expect(
    screen.getByRole("button", { name: "Propose 1 estimate" }),
  ).toBeDisabled();
});

it("never combines incomplete capability rows into proposal or review authority", () => {
  fixture.bankCapabilities.push(
    capability("configure", { sensitivity: "confidential" }),
    capability("view", {
      dataScope: { kind: "branch", branches: ["Head office"], regions: [] },
    }),
    capability("approve", { sensitivity: "confidential" }),
  );
  fixture.proposals = [pendingProposal()];
  mountPage();
  expect(
    screen.getByRole("button", { name: "Propose 1 estimate" }),
  ).toBeDisabled();
  expect(
    screen.queryByRole("button", { name: "Review and accept" }),
  ).toBeNull();
  expect(fixture.proposalReads).toBe(0);
});

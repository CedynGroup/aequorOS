// @vitest-environment jsdom
/**
 * Regulator queries: each officer is offered exactly the acts the server accepts.
 *
 * The live defect (2026-11-23): the page offered "Start the correction" and
 * "Send reply" to everyone, and the server refused both. The server now decides
 * on the return itself — the Validator (Regulatory Reporting / restricted /
 * submit) may open a correction and answer; the return's preparer (create, which
 * the Analyst bundle carries together with edit) may open a correction but not
 * answer. These cases render the real page with each officer's authority
 * projection and pin what is offered, and that nothing actionable renders before
 * the projection settles.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const BANK = "BK-SAMP0001";

const state = vi.hoisted(
  (): {
    permissions: string[] | undefined;
    kind: "query" | "resubmission";
  } => ({ permissions: [], kind: "resubmission" }),
);

vi.mock("@/components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({
    isLoading: false,
    error: null,
    profile: { userId: "user-officer" },
    effectiveAuthority:
      state.permissions === undefined
        ? undefined
        : {
            institutionCapabilities: [
              {
                institutionId: BANK,
                capabilities: state.permissions.map((permission) => ({
                  module: "reg",
                  sensitivity: "restricted",
                  permission,
                  requiresContextualAuthorization: permission === "submit",
                  dataScope: { kind: "all", branches: [], regions: [] },
                })),
              },
            ],
          },
  }),
}));

vi.mock("@/components/shell/BankContext", () => ({
  useBankContext: () => ({ bank: { id: BANK, name: "Sample Bank" } }),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
}));

const idle = { mutate: vi.fn(), isPending: false, isSuccess: false, isError: false };

vi.mock("@/lib/api/hooks", () => ({
  useRegulatorQueries: () => ({
    isPending: false,
    isError: false,
    data: {
      connected: true,
      message: null,
      readAt: new Date("2026-11-23T12:00:00Z"),
      items: [
        {
          number: "QRY-2026-0002",
          kind: state.kind,
          status: "awaiting_institution",
          subject: "Resubmit the capital return",
          receiptId: "SRG-26-0000001",
          openedAt: new Date("2026-11-23T10:00:00Z"),
          dueAt: null,
          // Already received, so opening it records nothing.
          acknowledgedAt: new Date("2026-11-23T10:05:00Z"),
          filing: {
            packageId: "00000000-0000-0000-0000-000000000001",
            returnCode: "CAR-RWA",
            reportingDate: new Date("2026-06-30T00:00:00Z"),
            version: 1,
          },
          messages: [
            {
              id: "m1",
              author: "supervisor",
              authorLabel: "Senior supervisor",
              body: "Please resubmit with the formula workbook.",
              createdAt: new Date("2026-11-23T10:00:00Z"),
            },
          ],
        },
      ],
    },
  }),
  useAcknowledgeRegulatorQuery: () => idle,
  useRespondToRegulatorQuery: () => idle,
  useStartRegulatorCorrection: () => idle,
}));

import RegulatorQueriesPage from "../../app/(app)/submissions/queries/page";

beforeEach(() => {
  state.permissions = [];
  state.kind = "resubmission";
});

afterEach(() => cleanup());

const correctionButton = () => screen.queryByRole("button", { name: "Start the correction" });
const replyBox = () => screen.queryByRole("textbox", { name: "Your reply" });

it("offers the Validator both the correction and the reply", () => {
  state.permissions = ["view", "submit"];
  render(<RegulatorQueriesPage />);

  expect(correctionButton()).toBeInTheDocument();
  expect(replyBox()).toBeInTheDocument();
  expect(screen.getByRole("button", { name: /Send reply to/ })).toBeInTheDocument();
});

it("offers the preparer the correction and explains who replies", () => {
  state.permissions = ["view", "create", "edit", "validate", "export"];
  render(<RegulatorQueriesPage />);

  expect(correctionButton()).toBeInTheDocument();
  expect(replyBox()).not.toBeInTheDocument();
  expect(screen.getByText(/Replies to .+ are sent by your Validator/)).toBeInTheDocument();
});

it("offers an officer with neither authority nothing to act on, and says why", () => {
  state.permissions = ["view"];
  render(<RegulatorQueriesPage />);

  expect(correctionButton()).not.toBeInTheDocument();
  expect(replyBox()).not.toBeInTheDocument();
  expect(
    screen.getByText(/The Validator or the preparer of CAR-RWA starts the correction/),
  ).toBeInTheDocument();
});

it("renders nothing actionable before the authority projection settles", () => {
  state.permissions = undefined;
  render(<RegulatorQueriesPage />);

  expect(screen.getByText("Please resubmit with the formula workbook.")).toBeInTheDocument();
  expect(correctionButton()).not.toBeInTheDocument();
  expect(replyBox()).not.toBeInTheDocument();
});

it("shows no correction step on an ordinary query", () => {
  state.permissions = ["view", "submit"];
  state.kind = "query";
  render(<RegulatorQueriesPage />);

  expect(correctionButton()).not.toBeInTheDocument();
  expect(replyBox()).toBeInTheDocument();
});

// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi, type Mock } from "vitest";

const state = vi.hoisted((): {
  userId: string;
  recipientUserId: string;
  exactInstitution: boolean;
  permissions: string[];
  attestationRecipientUserId: string;
  stepUp: Mock;
  attest: Mock;
  attestWithHeld: Mock;
  submit: Mock;
} => ({
  userId: "user-signer",
  recipientUserId: "user-signer",
  exactInstitution: true,
  permissions: [],
  attestationRecipientUserId: "",
  stepUp: vi.fn(),
  attest: vi.fn(),
  attestWithHeld: vi.fn(),
  submit: vi.fn(),
}));

vi.mock("@/components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({
    isLoading: false,
    error: null,
    profile: { userId: state.userId },
    effectiveAuthority: {
      organizationCapabilities: [],
      institutionCapabilities: [
        {
          institutionId: "BK-SAMPLE",
          capabilities: state.permissions.map((permission) => ({
            module: "risk",
            sensitivity:
              permission === "sign_off" ? "restricted" : "confidential",
            permission,
            exactInstitution:
              permission === "sign_off" ? state.exactInstitution : true,
            dataScope: { kind: "all", branches: [], regions: [] },
          })),
        },
      ],
    },
  }),
}));

vi.mock("@/lib/api/hooks", () => ({
  useOrganizationUsers: () => ({
    data: {
      users: [
        {
          id: "user-signer",
          email: "signer@example.test",
          displayName: "Routed Signer",
          isActive: true,
          role: "approver",
        },
        {
          id: "user-other",
          email: "other@example.test",
          displayName: "Other Officer",
          isActive: true,
          role: "approver",
        },
      ],
    },
    isLoading: false,
  }),
}));

vi.mock("@/components/stress/hooks", () => {
  const signoff = () => ({
    id: "signoff-1",
    organizationId: "OR-SAMPLE",
    bankId: "BK-SAMPLE",
    runId: "run-1",
    reportingPeriodId: "period-1",
    scenarioCode: "SYNTHETIC",
    status: state.attestationRecipientUserId ? "pending_attestation" : "draft",
    version: 1,
    scenarioNarrative: "Synthetic scenario",
    assumptionsRationale: "Synthetic rationale",
    methodologySummary: null,
    boardChallenge: null,
    credibilityRationale: null,
    staysAboveAllMinima: true,
    withActionsStaysAboveAllMinima: true,
    attestationRecipientUserId: state.attestationRecipientUserId || null,
    attestation_recipient_user_id: state.attestationRecipientUserId || null,
    signingDigest: state.attestationRecipientUserId ? "digest-1" : "",
    createdBy: "user-preparer",
    submittedBy: "user-preparer",
    submittedAt: null,
    attestedBy: null,
    attestedAt: null,
    createdAt: new Date("2026-03-31T00:00:00Z"),
    updatedAt: new Date("2026-03-31T00:00:00Z"),
    // Transitional wire spellings keep this fixture readable by the old panel;
    // the assertions below exercise the new generated-client ceremony.
    bank_id: "BK-SAMPLE",
    run_id: "run-1",
    scenario_code: "SYNTHETIC",
    scenario_narrative: "Synthetic scenario",
    assumptions_rationale: "Synthetic rationale",
    methodology_summary: null,
    board_challenge: null,
    credibility_rationale: null,
    signing_digest: state.attestationRecipientUserId ? "digest-1" : "",
  });
  const idle = () => ({
    isPending: false,
    error: null,
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
  });
  return {
    useBankStressSignoffs: () => ({
      data: [
        {
          id: "signoff-1",
          runId: "run-1",
          run_id: "run-1",
          reportingPeriodId: "period-1",
          scenarioCode: "SYNTHETIC",
          status: state.attestationRecipientUserId
            ? "pending_attestation"
            : "draft",
          version: 1,
          staysAboveAllMinima: true,
        },
      ],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    }),
    useStressSignoff: () => ({
      data: signoff(),
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    }),
    useCreateStressSignoff: idle,
    useUpdateStressSignoff: idle,
    useSubmitStressSignoff: () => ({ ...idle(), mutate: state.submit }),
    useAttestStressSignoff: () => ({
      ...idle(),
      mutateAsync: state.attest,
    }),
    useAttestStressSignoffWithHeldAuthorization: () => ({
      ...idle(),
      mutateAsync: state.attestWithHeld,
    }),
    useStressSignoffStepUp: () => ({
      ...idle(),
      mutateAsync: state.stepUp,
    }),
    useWithdrawStressSignoff: idle,
  };
});

vi.mock("@/components/ui/QueryBoundary", () => ({
  default: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));
vi.mock("@/components/ui/PermissionButton", () => ({
  PermissionButton: ({
    children,
    reason,
    ...props
  }: React.ButtonHTMLAttributes<HTMLButtonElement> & { reason?: string }) => (
    <button {...props} title={reason}>
      {children}
    </button>
  ),
}));

import SignoffPanel from "../../components/stress/SignoffPanel";

const run = { runId: "run-1", run_id: "run-1" };

function renderPanel() {
  return render(<SignoffPanel run={run} bankId="BK-SAMPLE" />);
}

beforeEach(() => {
  state.userId = "user-signer";
  state.recipientUserId = "user-signer";
  state.exactInstitution = true;
  state.attestationRecipientUserId = "user-signer";
  state.permissions = ["view"];
  state.stepUp.mockReset();
  state.stepUp.mockResolvedValue({
    authorizationToken: "one-shot-token",
    signingDigest: "digest-1",
  });
  state.attest.mockReset();
  state.attest.mockResolvedValue({});
  state.attestWithHeld.mockReset();
  state.attestWithHeld.mockResolvedValue({});
  state.submit.mockReset();
  window.history.replaceState({}, "", "/stress?run=run-1");
  window.sessionStorage.clear();
});

afterEach(cleanup);

it("hides step-up and attestation without an exact-bank Signer binding", () => {
  renderPanel();

  expect(screen.queryByLabelText("Your password")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /attest/i })).not.toBeInTheDocument();
  expect(screen.getByText(/Risk sign-off access with Restricted data/i)).toBeVisible();
});

it("hides step-up when the Signer lacks stress source-read authority", () => {
  state.permissions = ["sign_off"];
  renderPanel();

  expect(screen.queryByLabelText("Your password")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: /attest/i })).not.toBeInTheDocument();
  expect(
    screen.getByText(/Risk view access with Confidential data/i),
  ).toBeVisible();
});

it("hides step-up for an organization-wide Signer grant", () => {
  state.permissions = ["view", "sign_off"];
  state.exactInstitution = false;
  renderPanel();

  expect(screen.queryByLabelText("Your password")).not.toBeInTheDocument();
  expect(screen.getByText(/exact-institution scope/i)).toBeVisible();
});

it("hides step-up when the current Signer is not the named recipient", () => {
  state.permissions = ["view", "sign_off"];
  state.attestationRecipientUserId = "user-other";
  renderPanel();

  expect(screen.queryByLabelText("Your password")).not.toBeInTheDocument();
  expect(screen.getByText(/named recipient for this sign-off/i)).toBeVisible();
});

it("steps up and attests against the frozen digest as the named Signer", async () => {
  state.permissions = ["view", "sign_off"];
  renderPanel();

  fireEvent.change(screen.getByLabelText(/^Credibility rationale/), {
    target: { value: "The synthetic framework is credible." },
  });
  const ceremony = within(
    screen.getByRole("group", { name: "Board attestation" }),
  );
  fireEvent.change(ceremony.getByPlaceholderText("Reason (audit)"), {
    target: { value: "Synthetic attestation" },
  });
  fireEvent.change(screen.getByLabelText("Your password"), {
    target: { value: "synthetic-password" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Confirm identity and attest" }),
  );

  await waitFor(() =>
    expect(state.stepUp).toHaveBeenCalledWith({
      signoffId: "signoff-1",
      body: { password: "synthetic-password" },
    }),
  );
  await waitFor(() =>
    expect(state.attest).toHaveBeenCalledWith({
      signoffId: "signoff-1",
      body: {
        authorizationToken: "one-shot-token",
        expectedSigningDigest: "digest-1",
        credibilityRationale: "The synthetic framework is credible.",
        boardChallenge: null,
        reason: "Synthetic attestation",
      },
    }),
  );
});

it("attests with the server-held authorization after SSO step-up", async () => {
  state.permissions = ["view", "sign_off"];
  window.history.replaceState({}, "", "/stress?run=run-1&stepUp=ready");
  renderPanel();

  fireEvent.change(screen.getByLabelText(/^Credibility rationale/), {
    target: { value: "The synthetic SSO framework is credible." },
  });
  const ceremony = within(
    screen.getByRole("group", { name: "Board attestation" }),
  );
  fireEvent.change(ceremony.getByPlaceholderText("Reason (audit)"), {
    target: { value: "Synthetic SSO attestation" },
  });
  fireEvent.click(
    await screen.findByRole("button", { name: "Attest with single sign-on" }),
  );

  await waitFor(() =>
    expect(state.attestWithHeld).toHaveBeenCalledWith({
      signoffId: "signoff-1",
      body: {
        expectedSigningDigest: "digest-1",
        credibilityRationale: "The synthetic SSO framework is credible.",
        boardChallenge: null,
        reason: "Synthetic SSO attestation",
      },
    }),
  );
  expect(state.stepUp).not.toHaveBeenCalled();
  expect(state.attest).not.toHaveBeenCalled();
  expect(window.location.search).toBe("?run=run-1");
});

it("clears consumed SSO readiness when attestation fails", async () => {
  state.permissions = ["view", "sign_off"];
  state.attestWithHeld.mockRejectedValueOnce(new Error("expired"));
  window.history.replaceState({}, "", "/stress?run=run-1&stepUp=ready");
  renderPanel();

  fireEvent.change(screen.getByLabelText(/^Credibility rationale/), {
    target: { value: "Synthetic failed SSO rationale" },
  });
  const ceremony = within(
    screen.getByRole("group", { name: "Board attestation" }),
  );
  fireEvent.change(ceremony.getByPlaceholderText("Reason (audit)"), {
    target: { value: "Retry after consumed authorization" },
  });
  fireEvent.click(
    await screen.findByRole("button", { name: "Attest with single sign-on" }),
  );

  expect(
    await screen.findByRole("button", { name: "Use single sign-on" }),
  ).toBeEnabled();
  expect(
    screen.getByRole("button", { name: "Confirm identity and attest" }),
  ).toBeDisabled();
});

it("routes a draft submission to a named recipient", () => {
  state.permissions = ["view", "edit"];
  state.attestationRecipientUserId = "";
  renderPanel();

  fireEvent.change(screen.getByLabelText("Board sign-off recipient"), {
    target: { value: "user-signer" },
  });
  fireEvent.change(screen.getByPlaceholderText("Reason (audit)"), {
    target: { value: "Route synthetic sign-off" },
  });
  fireEvent.click(
    screen.getByRole("button", { name: /Submit for Board attestation/i }),
  );

  expect(state.submit).toHaveBeenCalledWith({
    signoffId: "signoff-1",
    body: {
      attestationRecipientUserId: "user-signer",
      reason: "Route synthetic sign-off",
    },
  });
});

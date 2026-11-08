// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import type { AttestationStatusRead } from "@aequoros/risk-service-api";
import { AttestationStatusReadFromJSON } from "@aequoros/risk-service-api";
import { setActiveJurisdiction } from "../../lib/format";

const state = vi.hoisted(() => ({
  userId: "user-signer",
  permissions: [] as string[],
  exactInstitution: true,
  attestationStatus: null as AttestationStatusRead | null,
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
            module: "reg",
            sensitivity: "restricted",
            permission,
            exactInstitution: state.exactInstitution,
            dataScope: { kind: "all", branches: [], regions: [] },
          })),
        },
      ],
    },
  }),
}));

vi.mock("@/components/shell/BankContext", () => ({
  useModuleScope: () => ({ capitalApprove: false }),
}));

vi.mock("@/lib/api/hooks", () => ({
  usePackageAttestation: () => ({
    isLoading: false,
    error: null,
    data: state.attestationStatus,
    refetch: vi.fn(),
  }),
  useVerifyPackageAttestation: () => ({
    isFetching: false,
    isLoading: false,
    error: null,
    data: null,
    refetch: vi.fn(),
  }),
  useVoidAttestation: () => ({
    isPending: false,
    error: null,
    reset: vi.fn(),
    mutate: vi.fn(),
  }),
  useOrganizationUsers: () => ({ data: { users: [] }, isLoading: false }),
  useUpdatePackageSignatureRouting: () => ({
    isPending: false,
    error: null,
    mutate: vi.fn(),
  }),
}));

// Next compiles local fonts at build time; jsdom has no font compiler.
vi.mock("next/font/local", () => ({
  default: () => ({ className: "local-font", style: { fontFamily: "serif" } }),
}));

// The ceremony surface itself is exercised by its own specs; here the panel's
// gating decision — whether the ceremony may open at all — is the behavior.
vi.mock("../../components/attestation/signing/SigningWorkspace", async (
  importOriginal,
) => ({
  ...(await importOriginal<
    Record<string, unknown>
  >()),
  default: ({
    signingRole,
    ssoOutcome,
  }: {
    signingRole: string;
    ssoOutcome: string | null;
  }) => (
    <div
      data-testid="signing-workspace"
      data-role={signingRole}
      data-sso-outcome={ssoOutcome ?? ""}
    />
  ),
}));

import AttestationPanel from "../../components/attestation/AttestationPanel";

const bankId = "BK-SAMPLE";

function statusFor(
  overrides: {
    attestationState?: string;
    outstanding?: Array<{ role: string; count: number }>;
    recipients?: Array<{
      signing_role: string;
      recipient_user_id: string;
      status: string;
    }>;
  } = {},
): AttestationStatusRead {
  return AttestationStatusReadFromJSON({
    package_id: "package-1",
    attestation_state: overrides.attestationState ?? "unsigned",
    attestation_cycle: 1,
    can_submit: false,
    certification_digest: "sha256:digest-1",
    certified_at: null,
    fully_certified_at: null,
    void_reason: null,
    voided_at: null,
    signatures: [],
    recipients: overrides.recipients ?? [],
    outstanding: overrides.outstanding ?? [{ role: "preparer", count: 1 }],
    policy: {
      source: "configured",
      policy_id: "policy-1",
      require_signature: true,
      require_signed_pdf: false,
      distinct_signers: true,
      required_signatures: [
        { role: "preparer", min_count: 1, officer_titles: [] },
      ],
      required_attachments: [],
    },
  });
}

function renderPanel(props: { packageStatus?: "validated" | "generated" } = {}) {
  return render(
    <AttestationPanel
      bankId={bankId}
      packageId="package-1"
      returnLabel="BSD3 · 31 Mar 2026"
      returnFamily="regulatory"
      packageStatus={props.packageStatus ?? "validated"}
      validationClean
    />,
  );
}

beforeEach(() => {
  setActiveJurisdiction({});
  state.userId = "user-signer";
  state.permissions = [];
  state.exactInstitution = true;
  state.attestationStatus = statusFor();
});

afterEach(cleanup);

it("fails closed: no sign-off authority shows the reason and offers no certify act", () => {
  state.permissions = ["view"];
  state.attestationStatus = statusFor({
    recipients: [
      {
        signing_role: "preparer",
        recipient_user_id: "user-signer",
        status: "pending",
      },
    ],
  });
  renderPanel();

  expect(
    screen.getByText(
      /Requires Regulatory Reporting sign-off access with Restricted data, exact-institution scope/,
    ),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Certify and freeze" }),
  ).not.toBeInTheDocument();
  expect(
    screen.queryByRole("button", { name: "Approve and sign" }),
  ).not.toBeInTheDocument();
});

it("fails closed when the Signer grant lacks independent source READ", () => {
  state.permissions = ["sign_off"];
  state.attestationStatus = statusFor({
    recipients: [
      {
        signing_role: "preparer",
        recipient_user_id: "user-signer",
        status: "pending",
      },
    ],
  });
  renderPanel();

  expect(
    screen.getByText(
      /Requires Regulatory Reporting view access with Restricted data and whole-institution coverage/,
    ),
  ).toBeVisible();
  expect(
    screen.queryByRole("button", { name: "Certify and freeze" }),
  ).not.toBeInTheDocument();
});

it("keeps the named preparer's certification disabled while nobody routed the signature", () => {
  state.permissions = ["view", "sign_off"];
  renderPanel();

  const certify = screen.getByRole("button", { name: "Certify and freeze" });
  expect(certify).toBeDisabled();
  expect(
    screen.getByText(
      /No pending current-cycle recipient names you for this signature/,
    ),
  ).toBeVisible();
});

it("opens the preparer ceremony for the named routed Signer", async () => {
  state.permissions = ["view", "sign_off"];
  state.attestationStatus = statusFor({
    recipients: [
      {
        signing_role: "preparer",
        recipient_user_id: "user-signer",
        status: "pending",
      },
    ],
  });
  const user = userEvent.setup();
  renderPanel();

  const certify = screen.getByRole("button", { name: "Certify and freeze" });
  expect(certify).toBeEnabled();
  await user.click(certify);

  expect(screen.getByTestId("signing-workspace")).toHaveAttribute(
    "data-role",
    "preparer",
  );
});

it("keeps the checker act away from a Signer who holds no REG approval", () => {
  state.permissions = ["view", "sign_off"];
  state.attestationStatus = statusFor({
    attestationState: "preparer_certified",
    outstanding: [{ role: "approver", count: 1 }],
    recipients: [
      {
        signing_role: "approver",
        recipient_user_id: "user-signer",
        status: "pending",
      },
    ],
  });
  renderPanel();

  expect(
    screen.queryByRole("button", { name: "Approve and sign" }),
  ).not.toBeInTheDocument();
  expect(
    screen.getByText(
      /Awaiting a checker signature\. Approving requires Regulatory Reporting approval/,
    ),
  ).toBeVisible();
});

it("opens the checker ceremony for a routed Signer who also holds REG approval", async () => {
  state.permissions = ["view", "sign_off", "approve"];
  state.attestationStatus = statusFor({
    attestationState: "preparer_certified",
    outstanding: [{ role: "approver", count: 1 }],
    recipients: [
      {
        signing_role: "approver",
        recipient_user_id: "user-signer",
        status: "pending",
      },
    ],
  });
  const user = userEvent.setup();
  renderPanel();

  const approve = screen.getByRole("button", { name: "Approve and sign" });
  expect(approve).toBeEnabled();
  await user.click(approve);

  expect(screen.getByTestId("signing-workspace")).toHaveAttribute(
    "data-role",
    "approver",
  );
});

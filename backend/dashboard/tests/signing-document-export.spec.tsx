// @vitest-environment jsdom
/**
 * The signing ceremony's page is the return's own file.
 *
 * Rendering the PDF in the workspace reads the artifact and archived-revision
 * download routes, which require REG/restricted EXPORT as well as the family
 * visibility and ceremony authority. These cases pin the boundary from the
 * officer's seat: a signing officer who cannot export is told which grant is
 * missing and downloads nothing, while the same officer with the exact
 * REG/restricted EXPORT row is served the document. No preview route, and no
 * automatic export attempt for a principal the server would refuse. Beyond the
 * grant, the ceremony also waits for the page to be drawn: certification is
 * what-you-see-is-what-you-sign, so a PDF that never painted cannot be signed.
 */
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

const state = vi.hoisted(() => ({
  permissions: [] as string[],
  exactInstitution: true,
  artifacts: [] as {
    id: string;
    kind: string;
    createdAt: Date;
    objectPath: string;
  }[],
  versions: [] as { id: string; isFiled: boolean }[],
  preview: undefined as unknown,
  previewCalls: 0,
  placementCalls: 0,
  adoptedSignatureCalls: 0,
  signerIdentityCalls: 0,
  routedNominations: [] as { signingRole: string; userId: string }[],
  status: undefined as unknown,
  statusLoading: false,
  statusError: null as Error | null,
  stepUpBlockedReason: null as string | null,
  /** Whether the mocked canvas reports a drawn page, as a real one would. */
  rendersPage: true,
  /** Whether the mocked canvas reports a real render failure instead. */
  renderFails: false,
  fetchArtifactBytes: vi.fn(),
  fetchArtifactVersionBytes: vi.fn(),
  exportMutate: vi.fn(),
}));

vi.mock("@/components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({
    isLoading: false,
    error: null,
    profile: { userId: "user-signer" },
    effectiveAuthority: {
      institutionCapabilities: [
        {
          institutionId: "BK-A",
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

vi.mock("@/components/submissions/shared", () => ({
  fetchArtifactBytes: state.fetchArtifactBytes,
  fetchArtifactVersionBytes: state.fetchArtifactVersionBytes,
}));

vi.mock("@/lib/api/grantAdministration", () => ({
  useGrantAdministrationAccess: () => false,
}));
vi.mock("@/components/impersonation/useImpersonation", () => ({
  useImpersonation: () => ({ impersonating: false }),
}));

vi.mock("@/lib/api/hooks", () => {
  const idle = () => ({
    mutate: vi.fn(),
    mutateAsync: vi.fn(),
    isPending: false,
    isError: false,
    error: null,
    reset: vi.fn(),
  });
  const settled = (data: unknown) => ({
    data,
    isLoading: false,
    isSuccess: true,
    error: null,
    refetch: vi.fn(),
  });
  return {
    useCertificationPreview: (
      _bankId: string,
      _packageId: string,
      _signingRole: string,
      enabled = true,
    ) => {
      if (enabled) state.previewCalls += 1;
      return settled(state.preview);
    },
    usePackageSignaturePlacements: (
      _bankId: string,
      _packageId: string,
      enabled = true,
    ) => {
      if (enabled) state.placementCalls += 1;
      return settled(undefined);
    },
    useMyAdoptedSignature: (enabled = true) => {
      if (enabled) state.adoptedSignatureCalls += 1;
      return settled(undefined);
    },
    useMySignerIdentity: (enabled = true) => {
      if (enabled) state.signerIdentityCalls += 1;
      return settled(undefined);
    },
    useOrganizationUsers: () => settled({ users: [] }),
    usePackageAttestation: () => ({
      ...settled(state.status),
      isLoading: state.statusLoading,
      error: state.statusError,
    }),
    usePackageArtifacts: () => settled({ artifacts: state.artifacts }),
    usePackageArtifactVersions: () => settled({ versions: state.versions }),
    useStepUpForSigning: idle,
    useCertifyPackage: idle,
    useCertifyWithHeldAuthorization: idle,
    useCertifyAndSend: idle,
    useCertifyAndSendWithHeldAuthorization: idle,
    useSetPackageSignaturePlacements: idle,
    useUpsertSignaturePlacementTemplate: idle,
    useSendBackForCorrections: idle,
    useExportRegulatoryPackage: () => ({
      ...idle(),
      mutate: state.exportMutate,
    }),
  };
});

vi.mock("@/components/attestation/signing/DocumentCanvas", async () => {
  const { useEffect } = await import("react");
  // The real canvas reports these as two separate events: the page count when
  // the PDF parses, and a drawn page only once the render promise resolves.
  // The mock keeps them separate for the same reason — a single "loaded" signal
  // is the defect this file exists to catch. `rendersPage` decides the paint.
  function MockDocumentCanvas({
    onDocumentLoaded,
    onPageDrawn,
    onPageFailed,
  }: {
    onDocumentLoaded: (n: number) => void;
    onPageDrawn?: () => void;
    onPageFailed?: () => void;
  }) {
    useEffect(() => {
      onDocumentLoaded(1);
      if (state.renderFails) onPageFailed?.();
      else if (state.rendersPage) onPageDrawn?.();
    }, [onDocumentLoaded, onPageDrawn, onPageFailed]);
    return <div data-testid="document-canvas" />;
  }
  return { default: MockDocumentCanvas };
});
vi.mock("@/components/attestation/signing/PlacementLayer", () => ({
  default: () => null,
}));
vi.mock("@/components/attestation/signing/FieldPalette", () => ({
  default: () => null,
  FIELD_DRAG_TYPE: "application/x-aequoros-signature-field",
}));
vi.mock("@/components/attestation/signing/AdoptSignaturePanel", () => ({
  default: () => null,
}));
vi.mock("@/components/attestation/signing/RecipientPicker", () => ({
  default: ({
    nominations,
  }: {
    nominations: { signingRole: string; userId: string }[];
  }) => {
    state.routedNominations = nominations;
    return null;
  },
}));
vi.mock("@/components/attestation/review", () => ({
  CertificationFailure: () => null,
  FiguresSection: () => null,
  PreviewError: () => null,
  SsoOutcomeNotice: () => null,
  StatementSection: () => null,
  // The proof buttons live here; what matters to this file is the reason the
  // workspace hands them, because a non-empty reason is what disables both.
  StepUpSection: (props: { blockedReason?: string | null }) => {
    state.stepUpBlockedReason = props.blockedReason ?? null;
    return null;
  },
  isTerminalFailure: () => false,
}));
vi.mock("@/components/attestation/shared", () => ({
  SIGNING_ROLE_ACTIONS: { preparer: "Certify", approver: "Approve" },
  SignatureBlock: () => null,
  roleNoun: (role: string) => role,
}));
vi.mock("@/components/ui/QueryBoundary", () => ({
  ErrorPanel: () => <div data-testid="document-error" />,
}));
vi.mock("@/components/ui/StatusPill", () => ({ default: () => null }));
vi.mock("@/components/ui/Skeleton", () => ({ SkeletonCard: () => null }));

import SigningWorkspace from "../components/attestation/signing/SigningWorkspace";
import CertifyDialog from "../components/attestation/CertifyDialog";

const PDF_ARTIFACT = {
  id: "artifact-1",
  kind: "pdf",
  createdAt: new Date("2026-01-01T00:00:00Z"),
  objectPath: "returns/ICAAP.pdf",
};

/**
 * Enough of the certification preview that the workspace reaches the proof
 * buttons: a preview with nothing outstanding renders the ceremony, so a
 * blocked certification is visible as a blocked one rather than as absence.
 */
const READY_PREVIEW = {
  outstanding: [],
  certificationDigest: "digest-1",
  statement: "I certify this return.",
};

beforeEach(() => {
  state.permissions = [];
  state.exactInstitution = true;
  state.artifacts = [];
  state.versions = [];
  state.preview = undefined;
  state.previewCalls = 0;
  state.placementCalls = 0;
  state.adoptedSignatureCalls = 0;
  state.signerIdentityCalls = 0;
  state.routedNominations = [];
  state.status = {
    signatures: [],
    recipients: [
      {
        signingRole: "approver",
        recipientUserId: "user-signer",
        status: "pending",
      },
    ],
  };
  state.statusLoading = false;
  state.statusError = null;
  state.stepUpBlockedReason = null;
  state.rendersPage = true;
  state.renderFails = false;
  state.fetchArtifactBytes.mockReset();
  state.fetchArtifactVersionBytes.mockReset();
  state.exportMutate.mockReset();
  state.fetchArtifactBytes.mockResolvedValue(new ArrayBuffer(0));
  state.fetchArtifactVersionBytes.mockResolvedValue(new ArrayBuffer(0));
});

afterEach(cleanup);

function renderWorkspace() {
  return render(
    <SigningWorkspace
      bankId="BK-A"
      packageId="package-1"
      signingRole="approver"
      returnLabel="ICAAP · 31 Mar 2026"
      onClose={() => undefined}
    />,
  );
}

it("aligns routed recipients to outstanding role order", async () => {
  state.permissions = ["view", "review", "approve", "sign_off"];
  state.status = {
    signatures: [],
    recipients: [
      {
        signingRole: "approver",
        recipientUserId: "user-signer",
        status: "pending",
      },
      {
        signingRole: "board",
        recipientUserId: "user-board",
        status: "pending",
      },
      {
        signingRole: "preparer",
        recipientUserId: "user-preparer",
        status: "pending",
      },
    ],
  };
  state.preview = {
    ...READY_PREVIEW,
    outstanding: [
      { role: "preparer", count: 1 },
      { role: "board", count: 1 },
      { role: "approver", count: 1 },
    ],
  };
  renderWorkspace();

  await waitFor(() =>
    expect(state.routedNominations).toEqual([
      { signingRole: "preparer", userId: "user-preparer" },
      { signingRole: "board", userId: "user-board" },
    ]),
  );
});

it.each([
  {
    failed: false,
    loading: true,
    message: "Checking who this signature is routed to…",
  },
  {
    failed: true,
    loading: false,
    message: "Could not load the current-cycle signing route. Reload the page to retry.",
  },
])(
  "keeps signing route status distinct from denial: $message",
  ({ failed, loading, message }) => {
    state.permissions = ["view", "review", "approve", "sign_off"];
    state.status = undefined;
    state.statusLoading = loading;
    state.statusError = failed ? new Error("synthetic status failure") : null;

    for (const kind of ["workspace", "dialog"] as const) {
      if (kind === "workspace") {
        renderWorkspace();
      } else {
        render(
          <CertifyDialog
            bankId="BK-A"
            packageId="package-1"
            signingRole="approver"
            returnLabel="ICAAP · 31 Mar 2026"
            onClose={() => undefined}
          />,
        );
      }
      expect(screen.getByRole("status")).toHaveTextContent(message);
      expect(
        screen.queryByText(/No pending current-cycle recipient names you/),
      ).toBeNull();
      expect(state.previewCalls).toBe(0);
      cleanup();
    }
  },
);

it("tells an approver without export which grant the document needs", async () => {
  state.permissions = ["view", "review", "approve", "sign_off"];
  state.artifacts = [PDF_ARTIFACT];
  state.preview = READY_PREVIEW;
  renderWorkspace();

  expect(screen.getByTestId("document-export-required")).toBeVisible();
  expect(
    screen.getByText(/Regulatory Reporting · Restricted · Export/),
  ).toBeVisible();
  expect(screen.queryByTestId("document-canvas")).not.toBeInTheDocument();
  // No bytes were requested and no artifact was minted for a principal the
  // server would refuse.
  expect(state.fetchArtifactBytes).not.toHaveBeenCalled();
  expect(state.fetchArtifactVersionBytes).not.toHaveBeenCalled();
  expect(state.exportMutate).not.toHaveBeenCalled();
  // The ceremony does not start on a page the officer cannot see: a non-empty
  // reason is what disables both the password proof and the SSO proof.
  expect(state.stepUpBlockedReason).toMatch(/Export/);
  expect(state.stepUpBlockedReason).toMatch(/Nothing has been signed/);
});

it("serves the page to a signing officer holding the exact export row", async () => {
  state.permissions = ["view", "review", "approve", "sign_off", "export"];
  state.artifacts = [PDF_ARTIFACT];
  state.preview = READY_PREVIEW;
  renderWorkspace();

  expect(
    screen.queryByTestId("document-export-required"),
  ).not.toBeInTheDocument();
  await waitFor(() =>
    expect(state.fetchArtifactBytes).toHaveBeenCalledWith("BK-A", "artifact-1"),
  );
  // The unsigned export already exists, so nothing is minted.
  expect(state.exportMutate).not.toHaveBeenCalled();
  // The page drew, so the authority no longer stands between this officer and
  // the ceremony.
  await waitFor(() => expect(state.stepUpBlockedReason).toBeNull());
});

it("keeps certification shut while the page has not drawn", async () => {
  state.permissions = ["view", "review", "approve", "sign_off", "export"];
  state.artifacts = [PDF_ARTIFACT];
  state.preview = READY_PREVIEW;
  // The bytes arrive and the canvas is on screen, but it never reports a page:
  // a PDF that failed to parse looks exactly like this.
  state.rendersPage = false;
  renderWorkspace();

  await waitFor(() =>
    expect(state.fetchArtifactBytes).toHaveBeenCalledWith("BK-A", "artifact-1"),
  );
  expect(screen.getByTestId("document-canvas")).toBeInTheDocument();
  await waitFor(() =>
    expect(state.stepUpBlockedReason).toMatch(/not on screen yet/),
  );
});

it("closes the ceremony again when the page render fails", async () => {
  state.permissions = ["view", "review", "approve", "sign_off", "export"];
  state.artifacts = [PDF_ARTIFACT];
  state.preview = READY_PREVIEW;
  // The PDF parses and the canvas mounts; the page then fails to draw. The
  // officer certifies what they see, and there is nothing to see.
  state.rendersPage = false;
  state.renderFails = true;
  renderWorkspace();

  await waitFor(() =>
    expect(state.fetchArtifactBytes).toHaveBeenCalledWith("BK-A", "artifact-1"),
  );
  await waitFor(() =>
    expect(state.stepUpBlockedReason).toMatch(/not on screen yet/),
  );
});

it("reads the filed revision once one exists", async () => {
  state.permissions = ["view", "review", "approve", "sign_off", "export"];
  state.artifacts = [PDF_ARTIFACT];
  state.versions = [{ id: "version-9", isFiled: true }];
  renderWorkspace();

  await waitFor(() =>
    expect(state.fetchArtifactVersionBytes).toHaveBeenCalledWith(
      "BK-A",
      "version-9",
    ),
  );
  expect(state.fetchArtifactBytes).not.toHaveBeenCalled();
});

it("keeps an Approver without Signer authority out of preview and step-up", async () => {
  state.permissions = ["view", "review", "approve", "export"];
  state.preview = READY_PREVIEW;
  renderWorkspace();

  expect(
    await screen.findByText(/Regulatory Reporting sign-off/),
  ).toBeVisible();
  expect(state.stepUpBlockedReason).toBeNull();
  expect(state.previewCalls).toBe(0);
  expect(state.placementCalls).toBe(0);
  expect(state.adoptedSignatureCalls).toBe(0);
  expect(state.signerIdentityCalls).toBe(0);
  expect(screen.queryByText("Send to")).not.toBeInTheDocument();
  expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
});

it("keeps a Signer without source-read authority out of preview and step-up", async () => {
  state.permissions = ["approve", "sign_off", "export"];
  state.preview = READY_PREVIEW;
  renderWorkspace();

  expect(
    await screen.findByText(/Regulatory Reporting view access with Restricted data/),
  ).toBeVisible();
  expect(state.stepUpBlockedReason).toBeNull();
  expect(state.previewCalls).toBe(0);
});

it("keeps an organization-wide Signer grant out of exact-bank signing", async () => {
  state.permissions = ["view", "approve", "sign_off", "export"];
  state.exactInstitution = false;
  state.preview = READY_PREVIEW;
  renderWorkspace();

  expect(await screen.findByText(/exact-institution scope/)).toBeVisible();
  expect(state.stepUpBlockedReason).toBeNull();
  expect(state.previewCalls).toBe(0);
});

it("keeps a checker without separate approval authority out of preview and step-up", async () => {
  state.permissions = ["view", "sign_off", "export"];
  state.preview = READY_PREVIEW;
  renderWorkspace();

  expect(
    await screen.findByText(/Regulatory Reporting approve access/),
  ).toBeVisible();
  expect(state.stepUpBlockedReason).toBeNull();
  expect(state.previewCalls).toBe(0);
});

it("keeps a Signer who is not the named recipient out of preview and step-up", async () => {
  state.permissions = ["view", "review", "approve", "sign_off", "export"];
  state.preview = READY_PREVIEW;
  state.status = {
    signatures: [],
    recipients: [
      {
        signingRole: "approver",
        recipientUserId: "another-user",
        status: "pending",
      },
    ],
  };
  renderWorkspace();

  expect(
    await screen.findByText(/not the named recipient/),
  ).toBeVisible();
  expect(state.stepUpBlockedReason).toBeNull();
  expect(state.previewCalls).toBe(0);
});

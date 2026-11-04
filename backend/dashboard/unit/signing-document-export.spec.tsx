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
  artifacts: [] as {
    id: string;
    kind: string;
    createdAt: Date;
    objectPath: string;
  }[],
  versions: [] as { id: string; isFiled: boolean }[],
  preview: undefined as unknown,
  stepUpBlockedReason: null as string | null,
  /** Whether the mocked canvas reports a drawn page, as a real one would. */
  rendersPage: true,
  fetchArtifactBytes: vi.fn(),
  fetchArtifactVersionBytes: vi.fn(),
  exportMutate: vi.fn(),
}));

vi.mock("@/components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({
    isLoading: false,
    effectiveAuthority: {
      institutionCapabilities: [
        {
          institutionId: "BK-A",
          capabilities: state.permissions.map((permission) => ({
            module: "reg",
            sensitivity: "restricted",
            permission,
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
    useCertificationPreview: () => settled(state.preview),
    usePackageSignaturePlacements: () => settled(undefined),
    useMyAdoptedSignature: () => settled(undefined),
    useMySignerIdentity: () => settled(undefined),
    useOrganizationUsers: () => settled({ users: [] }),
    usePackageAttestation: () => settled(undefined),
    usePackageArtifacts: () => settled({ artifacts: state.artifacts }),
    usePackageArtifactVersions: () => settled({ versions: state.versions }),
    useStepUpForSigning: idle,
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
  // Reports a drawn page the way the real canvas does, and stays silent when it
  // could not draw, which is the case the blocker has to survive.
  function MockDocumentCanvas({
    onDocumentLoaded,
  }: {
    onDocumentLoaded: (n: number) => void;
  }) {
    useEffect(() => {
      if (state.rendersPage) onDocumentLoaded(1);
    }, [onDocumentLoaded]);
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
  default: () => null,
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
  state.artifacts = [];
  state.versions = [];
  state.preview = undefined;
  state.stepUpBlockedReason = null;
  state.rendersPage = true;
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

it("tells an approver without export which grant the document needs", async () => {
  state.permissions = ["view", "review", "approve"];
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
  state.permissions = ["view", "review", "approve", "export"];
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
  state.permissions = ["view", "review", "approve", "export"];
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

it("reads the filed revision once one exists", async () => {
  state.permissions = ["view", "review", "export"];
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

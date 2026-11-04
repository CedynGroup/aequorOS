// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import FilingWorkspace from "../components/icaap/p3/FilingWorkspace";

const profile = vi.hoisted(() => ({
  isLoading: false,
  module: "reg",
  sensitivity: "restricted",
  institutionId: "BK-A",
  files: [] as {
    id: string;
    kind: string;
    sizeBytes: number | null;
    createdAt: string;
  }[],
  revisions: [] as {
    id: string;
    signedByName: string | null;
    signedByTitle: string | null;
    signedAt: string | null;
    createdAt: string;
    isFiled: boolean;
  }[],
}));
vi.mock("@/components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({
    isLoading: profile.isLoading,
    effectiveAuthority: {
      institutionCapabilities: [
        {
          institutionId: profile.institutionId,
          capabilities: [
            {
              module: profile.module,
              sensitivity: profile.sensitivity,
              permission: "export",
            },
          ],
        },
      ],
    },
  }),
}));
vi.mock("@/components/shell/BankContext", () => ({
  useModuleScope: () => ({
    capitalEdit: true,
    capitalApprove: true,
    capitalExport: true,
  }),
}));
vi.mock("@/lib/api/icaapFiling", () => ({
  useIcaapFiling: () => ({
    data: {
      package: {
        id: "package",
        status: "generated",
        returnCode: "ICAAP",
        reportingDate: null,
      },
      submittable: false,
    },
  }),
  useIcaapStages: () => ({}),
  useIcaapPackageArtifacts: () => ({ data: profile.files }),
  useIcaapPackageArtifactVersions: () => ({ data: profile.revisions }),
}));
vi.mock("@/lib/api/hooks", () => ({
  useRegulatoryPackage: () => ({}),
  useValidateRegulatoryPackage: () => ({ isPending: false, mutate: vi.fn() }),
  useExportRegulatoryPackage: () => ({ isPending: false, mutate: vi.fn() }),
}));
vi.mock("@/components/submissions/shared", () => ({
  PackageStatusPill: () => null,
  downloadArtifact: vi.fn(),
  downloadArtifactVersion: vi.fn(),
  fmtBytes: () => "",
}));
vi.mock("@/components/submissions/FilingChain", () => ({
  PackageChainStrip: () => null,
}));
vi.mock("@/components/icaap/p3/SigningCard", () => ({ default: () => null }));
vi.mock("@/components/icaap/p3/PackageAttachmentsCard", () => ({
  default: () => null,
}));
vi.mock("@/components/icaap/p3/SubmitIcaapCard", () => ({
  default: () => null,
}));
vi.mock("@/components/icaap/p3/DisclosurePanel", () => ({
  default: () => null,
}));
vi.mock("@/components/icaap/p3/StageActions", () => ({
  ReturnFromFilingDialog: () => null,
}));

afterEach(() => {
  cleanup();
  profile.files = [];
  profile.revisions = [];
});

const SEALED_FILE = {
  id: "file",
  kind: "pdf",
  sizeBytes: 1024,
  createdAt: "2026-01-01T00:00:00Z",
};
const SIGNED_REVISION = {
  id: "revision",
  signedByName: "A. Officer",
  signedByTitle: "CFO",
  signedAt: "2026-01-02T00:00:00Z",
  createdAt: "2026-01-02T00:00:00Z",
  isFiled: true,
};

it.each([
  ["capital", "confidential", "BK-A", false, false],
  ["reg", "confidential", "BK-A", false, false],
  ["reg", "restricted", "BK-B", false, false],
  ["reg", "restricted", "BK-A", true, false],
  ["reg", "restricted", "BK-A", false, true],
] as const)(
  "offers ICAAP return working copies for %s/%s at %s (loading=%s): %s",
  (module, sensitivity, institutionId, isLoading, offered) => {
    Object.assign(profile, { module, sensitivity, institutionId, isLoading });
    render(
      <FilingWorkspace
        bankId="BK-A"
        cycleId="cycle"
        cycleKind="annual"
        cycleTitle="ICAAP"
      />,
    );
    expect(screen.getByText("Checks on the sealed report")).toBeVisible();
    const workingCopy = screen.queryByRole("button", {
      name: "Produce a Word working copy",
    });
    if (offered) expect(workingCopy).toBeEnabled();
    else expect(workingCopy).not.toBeInTheDocument();
  },
);

it.each([
  ["capital", "confidential", "BK-A", 0],
  ["reg", "confidential", "BK-A", 0],
  ["reg", "restricted", "BK-B", 0],
  ["reg", "restricted", "BK-A", 2],
] as const)(
  "offers ICAAP return file downloads for %s/%s at %s: %i",
  (module, sensitivity, institutionId, downloads) => {
    Object.assign(profile, {
      module,
      sensitivity,
      institutionId,
      isLoading: false,
    });
    profile.files = [SEALED_FILE];
    profile.revisions = [SIGNED_REVISION];
    render(
      <FilingWorkspace
        bankId="BK-A"
        cycleId="cycle"
        cycleKind="annual"
        cycleTitle="ICAAP"
      />,
    );
    // The reader keeps every fact about the documents they may not take away.
    expect(screen.getByText("The sealed report")).toBeVisible();
    expect(screen.getByText("A. Officer, CFO")).toBeVisible();
    expect(screen.getByText("The filed document")).toBeVisible();
    // One for the sealed file, one for the signed revision; neither is offered
    // to a principal the server would refuse.
    expect(screen.queryAllByRole("button", { name: "Download" })).toHaveLength(
      downloads,
    );
    const reason = screen.queryByText(
      "Downloading a return file requires Regulatory Reporting · Restricted · Export.",
    );
    if (downloads === 0) expect(reason).toBeVisible();
    else expect(reason).not.toBeInTheDocument();
  },
);

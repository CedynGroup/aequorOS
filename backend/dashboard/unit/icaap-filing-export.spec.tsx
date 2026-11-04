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
  useIcaapPackageArtifacts: () => ({ data: [] }),
  useIcaapPackageArtifactVersions: () => ({ data: [] }),
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

afterEach(cleanup);

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

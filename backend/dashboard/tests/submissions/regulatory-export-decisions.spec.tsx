// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import {
  IcaapCycleReadFromJSON,
  PackageVersionChainReadFromJSON,
  RegulatoryPackageSummaryReadFromJSON,
} from "@aequoros/risk-service-api";
import CycleHeader from "@/components/icaap/CycleHeader";
import FilingDownloads from "@/components/icaap/p3/FilingDownloads";
import PriorVersionsCard from "@/components/submissions/PriorVersionsCard";
import HistoryPage from "@/app/(app)/submissions/history/page";
import RegulatoryExportWarning from "@/components/submissions/RegulatoryExportWarning";
import { frameworkInstrumentStatus } from "@/lib/regulatoryInstruments";
import { setActiveJurisdiction } from "@/lib/format";

const state = vi.hoisted(() => ({
  snapshots: {} as Record<string, Record<string, unknown> | undefined>,
  canExport: true,
  downloadDraft: vi.fn().mockResolvedValue(undefined),
  downloadArtifact: vi.fn().mockResolvedValue(undefined),
  downloadRevision: vi.fn().mockResolvedValue(undefined),
  downloadEmail: vi.fn().mockResolvedValue(undefined),
  produceWord: vi.fn(),
}));
const bankId = "BK-SYNTH001";
const timestamp = "2026-10-23T12:00:00Z";
const artifact = {
  id: "artifact",
  packageId: "archived",
  kind: "pdf",
  objectPath: "report.pdf",
  checksumSha256: "synthetic-checksum",
  sizeBytes: 100,
  createdAt: timestamp,
};
const revision = {
  ...artifact,
  id: "revision",
  signedByName: "Synthetic officer",
  signedByTitle: null,
  signedAt: timestamp,
  isFiled: true,
};
function summary() {
  return RegulatoryPackageSummaryReadFromJSON({
    id: "archived",
    return_code: "SYNTH-RETURN",
    return_family: "icaap_report",
    reporting_date: "2026-09-30",
    generated_at: timestamp,
    generated_by: "officer",
    version: 1,
    basis: "solo",
    status: "generated",
    current_stage_seq: null,
  });
}
function settled<T>(data: T) {
  return {
    data,
    error: null,
    isLoading: false,
    isFetching: false,
    refetch: vi.fn(),
  };
}
function snapshot(status: string) {
  return {
    provenance: {
      instrument_status: status,
      instrument_effective_from: "2027-01-01",
    },
  };
}
vi.mock("@/components/shell/BankContext", () => ({
  useBankContext: () => ({ bank: { id: bankId } }),
  useModuleScope: () => ({ capitalExport: state.canExport }),
}));
vi.mock("@/components/profile/ProfileProvider", () => ({
  useUserProfile: () => ({ effectiveAuthority: undefined }),
}));
vi.mock("@/lib/api/icaap", () => ({
  downloadIcaapDraft: (...args: unknown[]) => state.downloadDraft(...args),
}));
vi.mock("@/lib/api/hooks", () => ({
  useExportRegulatoryPackage: () => ({
    isPending: false,
    mutate: state.produceWord,
  }),
  useRegulatoryPackage: (_bank: string | undefined, id: string) =>
    settled({
      snapshot: state.snapshots[id],
      approvals: [],
      sourceRuns: [],
    }),
  usePackageArtifacts: () => settled({ artifacts: [artifact] }),
  useRegulatoryPackages: () =>
    settled({ packages: [summary()], total: 1, hasMore: false }),
  useOfficerNames: () => (id: string) => id,
  usePackageAttestation: () => settled(undefined),
  useResubmissionRequests: () => settled({ requests: [] }),
  useSubmissionEvents: () =>
    settled({
      events: [
        {
          id: "event",
          event: "submitted",
          channel: "email",
          occurredAt: new Date(timestamp),
        },
      ],
    }),
  useComparePackageVersions: () => settled(undefined),
  usePackageVersionChain: () =>
    settled(
      PackageVersionChainReadFromJSON({
        current_package_id: "current",
        versions: [
          {
            package_id: "current",
            version: 2,
            is_current: true,
            artifact_versions: [],
            artifacts: [],
            signatures: [],
          },
          {
            package_id: "archived",
            version: 1,
            is_current: false,
            status: "superseded",
            basis: "solo",
            generated_at: timestamp,
            reporting_date: "2026-09-30",
            has_retrievable_files: true,
            artifacts: [
              {
                ...artifact,
                checksum_sha256: "synthetic-checksum",
                size_bytes: 100,
                created_at: timestamp,
              },
            ],
            artifact_versions: [
              {
                ...revision,
                checksum_sha256: "synthetic-checksum",
                size_bytes: 100,
                created_at: timestamp,
              },
            ],
            signatures: [],
            attestation_state: "unsigned",
          },
        ],
      }),
    ),
}));
vi.mock("@/lib/api/icaapFiling", () => ({
  useIcaapPackageArtifacts: () => settled([artifact]),
  useIcaapPackageArtifactVersions: () => settled([revision]),
}));
vi.mock("@/components/submissions/shared", async (importOriginal) => ({
  ...(await importOriginal<typeof import("@/components/submissions/shared")>()),
  downloadArtifact: (...args: unknown[]) => state.downloadArtifact(...args),
  downloadArtifactVersion: (...args: unknown[]) =>
    state.downloadRevision(...args),
  downloadEmailFallbackEml: (...args: unknown[]) =>
    state.downloadEmail(...args),
}));
beforeEach(() => {
  vi.clearAllMocks();
  state.canExport = true;
  state.snapshots = {
    archived: snapshot("exposure_draft"),
    current: snapshot("in_force"),
  };
});
afterEach(() => {
  cleanup();
  setActiveJurisdiction({});
});

it.each([
  ["final", "2000-01-01", "in_force"],
  ["final", "2099-01-01", "final_not_in_force"],
  ["exposure_draft", "2000-01-01", "exposure_draft"],
  ["final", null, undefined],
] as const)(
  "normalizes framework %s effective %s",
  (status, effective, expected) => {
    expect(frameworkInstrumentStatus(status, effective, "2026-10-24")).toBe(
      expected,
    );
  },
);

it.each(["final", "exposure_draft"])(
  "uses the recorded %s framework on cycle and package exports",
  (status) => {
    const cycle = IcaapCycleReadFromJSON({
      id: "cycle",
      title: "Synthetic final framework",
      fiscal_year: 2026,
      basis: "solo",
      cycle_kind: "annual",
      status: "draft",
      sections: [],
      framework: {
        code: "own-framework",
        status,
        effective_from: "2000-01-01",
      },
    });
    state.canExport = false;
    const view = render(<CycleHeader bankId={bankId} cycle={cycle} />);
    expect(Boolean(screen.queryByRole("note"))).toBe(
      status === "exposure_draft",
    );
    expect(screen.getByRole("button", { name: "Draft PDF" })).toBeDisabled();
    view.rerender(
      <RegulatoryExportWarning
        snapshot={{
          ...snapshot(status === "final" ? "exposure_draft" : "in_force"),
          metadata: {
            icaap: { framework: { status, effective_from: "2000-01-01" } },
          },
        }}
      />,
    );
    expect(Boolean(screen.queryByRole("note"))).toBe(
      status === "exposure_draft",
    );
    if (status === "exposure_draft")
      expect(screen.getByRole("note")).toHaveTextContent("not law yet");
  },
);

it("discloses the cycle's own framework beside both header draft downloads", async () => {
  setActiveJurisdiction({ regulatorShort: "CBK" });
  const user = userEvent.setup();
  const cycle = IcaapCycleReadFromJSON({
    id: "cycle",
    title: "Synthetic cycle",
    fiscal_year: 2026,
    basis: "solo",
    cycle_kind: "rehearsal",
    status: "draft",
    editable: true,
    sections: [],
    framework: {
      code: "own-framework",
      status: "exposure_draft",
      effective_from: "2027-01-01",
      regulator: "Recorded issuer",
    },
  });
  const { rerender } = render(<CycleHeader bankId={bankId} cycle={cycle} />);
  expect(screen.getByRole("note")).toHaveTextContent("not law yet");
  expect(screen.getByRole("note")).toHaveTextContent(
    "Proposed by Recorded issuer",
  );
  expect(screen.getByRole("note")).toHaveTextContent("2027-01-01");
  await user.click(screen.getByRole("button", { name: "Draft PDF" }));
  await user.click(
    screen.getByRole("button", { name: "Draft Word (working copy)" }),
  );
  expect(state.downloadDraft.mock.calls).toEqual([
    [bankId, "cycle", "pdf", "working"],
    [bankId, "cycle", "docx", "working"],
  ]);
  state.canExport = false;
  rerender(<CycleHeader bankId={bankId} cycle={cycle} />);
  expect(screen.getByRole("button", { name: "Draft PDF" })).toBeDisabled();
});

it("discloses the sealed ICAAP framework for files, signed revisions and Word production", async () => {
  state.snapshots.archived = {
    ...snapshot("in_force"),
    metadata: {
      icaap: {
        framework: { status: "exposure_draft", effective_from: "2027-01-01" },
      },
    },
  };
  const user = userEvent.setup();
  render(<FilingDownloads bankId={bankId} packageId="archived" canExport />);
  expect(screen.getByRole("note")).toHaveTextContent("not law yet");
  const buttons = screen.getAllByRole("button", { name: "Download" });
  await user.click(buttons[0]);
  await user.click(buttons[1]);
  await user.click(
    screen.getByRole("button", { name: "Produce a Word working copy" }),
  );
  expect(state.downloadArtifact).toHaveBeenCalledWith(bankId, artifact);
  expect(state.downloadRevision).toHaveBeenCalledWith(bankId, revision);
  expect(state.produceWord).toHaveBeenCalledWith(
    { packageId: "archived", kind: "docx_working" },
    expect.any(Object),
  );
});

it("uses the selected history package's basis for artifact and email downloads", async () => {
  const user = userEvent.setup();
  render(<HistoryPage />);
  fireEvent.click(screen.getByText("SYNTH-RETURN").closest("tr")!);
  expect(screen.getByRole("note")).toHaveTextContent("not law yet");
  await user.click(screen.getByRole("button", { name: "Download" }));
  await user.click(screen.getByRole("button", { name: "Download .eml" }));
  expect(state.downloadArtifact).toHaveBeenCalledWith(bankId, artifact);
  expect(state.downloadEmail).toHaveBeenCalledWith(bankId, "archived");
});

it("keeps historical file warnings independent of the current package in both directions", async () => {
  const user = userEvent.setup();
  const { rerender } = render(
    <PriorVersionsCard bankId={bankId} packageId="current" />,
  );
  await user.click(screen.getByRole("button", { expanded: false }));
  expect(screen.getByRole("note")).toHaveTextContent("not law yet");
  await user.click(
    screen.getByRole("button", { name: "Download v1 pdf revision" }),
  );
  await user.click(
    screen.getByRole("button", { name: "Download v1 pdf export" }),
  );
  expect(state.downloadRevision).toHaveBeenCalledTimes(1);
  expect(state.downloadArtifact).toHaveBeenCalledTimes(1);
  state.snapshots = {
    archived: snapshot("in_force"),
    current: snapshot("exposure_draft"),
  };
  rerender(<PriorVersionsCard bankId={bankId} packageId="current" />);
  expect(screen.queryByRole("note")).toBeNull();
  state.snapshots.archived = undefined;
  rerender(<PriorVersionsCard bankId={bankId} packageId="current" />);
  expect(screen.getByRole("note")).toHaveTextContent(
    "has not been established",
  );
});

it.each([
  "exposure_draft",
  "final_not_in_force",
  "unpublished",
  "in_force",
  undefined,
])("renders the shared export disclosure for recorded status %s", (status) => {
  render(<RegulatoryExportWarning snapshot={status ? snapshot(status) : {}} />);
  if (status === "in_force") expect(screen.queryByRole("note")).toBeNull();
  else
    expect(screen.getByRole("note")).toHaveTextContent(
      "does not establish compliance",
    );
});

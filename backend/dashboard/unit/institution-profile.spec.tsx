// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import type { EffectiveCapabilityRead } from "@aequoros/risk-service-api";
import InstitutionProfilePage from "../app/(app)/institution/page";

const fixture = vi.hoisted(() => ({
  capabilities: [] as EffectiveCapabilityRead[],
  save: vi.fn(),
  anchors: vi.fn(() => ({ data: undefined })),
}));
vi.mock("../components/shell/BankContext", () => ({
  useBankContext: () => ({
    bank: { id: "BK-TEST0001" },
    institutionCapabilities: fixture.capabilities,
    authorityPending: false,
  }),
}));
vi.mock("../lib/api/hooks", () => ({
  useInstitutionProfile: () => ({
    data: {
      generalVisible: true,
      restrictedVisible: true,
      profile: {
        id: "profile",
        bankId: "BK-TEST0001",
        institutionType: "Synthetic bank",
        legalEntityStructure: "Synthetic company",
        authorisationDate: null,
        approvedCapital: null,
        incorporationDate: null,
        tradedOnExchange: false,
        exchangeName: null,
        isin: null,
        ownershipLocalPct: null,
        ownershipForeignPct: null,
        parentCountryCode: null,
        warnings: [],
        createdAt: new Date("2026-10-01"),
        updatedAt: new Date("2026-10-01"),
      },
      identifiers: {
        tin: "SYNTHETIC-TAX",
        registrationNumber: "SYNTHETIC-REG",
        orassInstitutionCode: "SYNTHETIC-PORTAL",
      },
    },
    isLoading: false,
    error: null,
    refetch: vi.fn(),
  }),
  useSaveInstitutionProfile: () => ({ mutate: fixture.save, isPending: false }),
  useReturnAnchors: fixture.anchors,
}));
afterEach(() => {
  cleanup();
  fixture.capabilities = [];
  vi.clearAllMocks();
});
function grant(
  sensitivity: "confidential" | "restricted",
  permission: "view" | "edit",
): EffectiveCapabilityRead {
  return {
    module: "institution",
    sensitivity,
    permission,
    requiresContextualAuthorization: false,
    dataScope: { kind: "all", branches: [], regions: [] },
  };
}
it("omits read-only restricted identifiers when saving general profile details", () => {
  fixture.capabilities = [
    grant("confidential", "view"),
    grant("confidential", "edit"),
    grant("restricted", "view"),
  ];
  render(<InstitutionProfilePage />);
  expect(screen.getByText("SYNTHETIC-TAX")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Edit profile" }));
  expect(screen.queryByLabelText("TIN")).not.toBeInTheDocument();
  expect(
    screen.queryByLabelText("Registration number"),
  ).not.toBeInTheDocument();
  fireEvent.change(screen.getByLabelText(/Reason for change/), {
    target: { value: "Synthetic general edit" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Save profile" }));
  expect(fixture.save).toHaveBeenCalledOnce();
  const payload: unknown = fixture.save.mock.calls[0]?.[0];
  expect(payload).not.toHaveProperty("tin");
  expect(payload).not.toHaveProperty("registrationNumber");
  expect(payload).not.toHaveProperty("orassInstitutionCode");
  expect(payload).toHaveProperty("reason", "Synthetic general edit");
  expect(fixture.anchors).toHaveBeenCalledWith(undefined, "LRT-PROFILE");
});
it("hides identifiers and disables editing for a confidential-only reader", () => {
  fixture.capabilities = [grant("confidential", "view")];
  render(<InstitutionProfilePage />);
  expect(screen.queryByText("SYNTHETIC-TAX")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Edit profile" })).toBeDisabled();
  expect(
    screen.getByText(/Requires Institution · Restricted · View/),
  ).toBeInTheDocument();
});

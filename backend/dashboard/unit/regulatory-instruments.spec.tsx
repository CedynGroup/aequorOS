// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import fc from "fast-check";
import InstrumentStatusBadge from "@/components/submissions/InstrumentStatusBadge";
import RegulatoryExportWarning from "@/components/submissions/RegulatoryExportWarning";
import { setActiveJurisdiction } from "@/lib/format";
import { instrumentStatusLabel } from "@/lib/api/returnAnchors";
import {
  instrumentDisclosure,
  isUpcomingRule,
  preparationWarning,
  packagePreparationWarning,
} from "@/lib/regulatoryInstruments";

afterEach(() => {
  cleanup();
  setActiveJurisdiction({});
});

const statuses = [
  "exposure_draft",
  "final_not_in_force",
  "in_force",
  "unpublished",
] as const;

it("renders distinct status colours without disclosure markers and keeps explanations in flow", () => {
  const { container } = render(
    <>
      {statuses.map((status) => (
        <InstrumentStatusBadge key={status} status={status} code="SYNTHETIC" />
      ))}
    </>,
  );
  const badges = [...container.querySelectorAll("summary")];
  expect(new Set(badges.map((badge) => badge.className)).size).toBe(4);
  for (const badge of badges) expect(badge).toHaveClass("list-none");
  const badge = screen.getByText("Draft");
  fireEvent.click(badge);
  const explanation = screen.getAllByRole("tooltip")[0];
  expect(explanation).not.toHaveClass("absolute");
  expect(
    screen.getAllByRole("link", {
      name: "Source details in the instruments register",
    })[0],
  ).toHaveAttribute("href", "/submissions/instruments#SYNTHETIC");
});

describe("regulatory status", () => {
  it("keeps publication status separate from dates", () => {
    fc.assert(
      fc.property(fc.date({ noInvalidDate: true }), (date) => {
        expect(instrumentDisclosure("exposure_draft", date).label).toBe(
          "Draft",
        );
        expect(instrumentDisclosure("exposure_draft", date).tooltip).toContain(
          "if made final",
        );
        expect(isUpcomingRule("exposure_draft")).toBe(true);
        expect(isUpcomingRule("unpublished")).toBe(false);
        expect(preparationWarning("in_force", date)).toBeNull();
      }),
    );
  });

  it.each(statuses)(
    "uses the same copy and exposes citation and a keyboard-accessible source for %s",
    async (status) => {
      const user = userEvent.setup();
      render(
        <InstrumentStatusBadge
          status={status}
          effectiveFrom="2027-01-01"
          citation="Synthetic regulator citation"
          sourceUrl="https://example.test/rule"
        />,
      );
      const disclosure = instrumentDisclosure(status, "2027-01-01");
      const badge = screen.getByText(disclosure.label);
      expect(badge).toHaveAttribute("title", disclosure.tooltip);
      await user.click(badge);
      expect(screen.getByRole("tooltip")).toHaveTextContent(disclosure.tooltip);
      expect(screen.getByText("Synthetic regulator citation")).toBeVisible();
      expect(
        screen.getByRole("link", { name: "Read the source" }),
      ).toHaveAttribute("href", "https://example.test/rule");
      await user.tab();
      expect(
        screen.getByRole("link", { name: "Read the source" }),
      ).toHaveFocus();
    },
  );

  it("does not invent a commencement date", () => {
    expect(instrumentDisclosure("final_not_in_force").tooltip).toContain(
      "Start date not published",
    );
    expect(preparationWarning("exposure_draft")).toContain(
      "does not establish compliance",
    );
    expect(instrumentDisclosure(undefined).label).toBe("Status unavailable");
  });
});

it("keeps an archived draft pack's warning after the live instrument commences", () => {
  const warning = packagePreparationWarning(
    {
      provenance: {
        instrument_status: "exposure_draft",
        instrument_effective_from: "2027-01-01",
      },
    },
    "in_force",
    "2027-01-01",
  );
  expect(warning).toContain("not law yet");
  expect(packagePreparationWarning({}, "in_force")).toContain(
    "has not been established",
  );
});

it.each([
  {
    provenance: {
      instrument_status: "exposure_draft",
      regulator: "Recorded issuer",
    },
  },
  {
    metadata: {
      icaap: {
        regulator_short: "Recorded issuer",
        framework: { status: "exposure_draft", effective_from: "2027-01-01" },
      },
    },
  },
])(
  "uses the sealed package issuer despite current bank context",
  (snapshot) => {
    setActiveJurisdiction({ regulatorShort: "CBK" });
    render(
      <RegulatoryExportWarning
        snapshot={snapshot}
        status="exposure_draft"
        regulator="Current issuer"
      />,
    );
    expect(screen.getByRole("note")).toHaveTextContent(
      "Proposed by Recorded issuer",
    );
    expect(screen.getByRole("note")).not.toHaveTextContent(
      /CBK|Current issuer/,
    );
  },
);

it("passes recorded issuers through generation warnings and legacy labels", () => {
  setActiveJurisdiction({ regulatorShort: "CBK" });
  render(
    <RegulatoryExportWarning
      status="exposure_draft"
      regulator="Recorded issuer"
    />,
  );
  expect(screen.getByRole("note")).toHaveTextContent(
    "Proposed by Recorded issuer",
  );
  expect(
    preparationWarning("exposure_draft", undefined, "Recorded issuer"),
  ).toContain("Proposed by Recorded issuer");
  expect(
    instrumentStatusLabel("unpublished", undefined, "Recorded issuer"),
  ).toContain("No Recorded issuer filing duty");
});

it("does not infer an issuer when recorded evidence is missing", () => {
  setActiveJurisdiction({ regulatorShort: "CBK" });
  expect(instrumentDisclosure("exposure_draft").tooltip).toContain(
    "Proposed by the regulator",
  );
  expect(
    packagePreparationWarning(
      { provenance: { instrument_status: "exposure_draft" } },
      "exposure_draft",
      undefined,
      "Current issuer",
    ),
  ).toContain("Proposed by the regulator");
});

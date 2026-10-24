// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import fc from "fast-check";
import InstrumentStatusBadge from "@/components/submissions/InstrumentStatusBadge";
import {
  instrumentDisclosure,
  isUpcomingRule,
  preparationWarning,
  packagePreparationWarning,
} from "@/lib/regulatoryInstruments";

afterEach(cleanup);

const statuses = [
  "exposure_draft",
  "final_not_in_force",
  "in_force",
  "unpublished",
] as const;

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
      "not established",
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

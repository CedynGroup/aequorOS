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
  instrumentDateLabel,
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

it("formats a recorded calendar date from its own parts", () => {
  expect(instrumentDateLabel("2027-01-01")).toBe("1 Jan 2027");
  expect(instrumentDateLabel("2026-12-31")).toBe("31 Dec 2026");
  expect(instrumentDateLabel("not-a-date")).toBe("not-a-date");
});

it("renders distinct status colours and floats the disclosure over neighbouring content", () => {
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
  expect(badge).toHaveClass("truncate", "max-w-full");
  fireEvent.click(badge);
  const explanation = screen.getAllByTestId("instrument-status-disclosure")[0];
  // Viewport-anchored so a 160px register cell or an overflow-hidden card
  // cannot collapse or clip it.
  expect(explanation).toHaveStyle({ position: "fixed", width: "288px" });
  expect(explanation.parentElement).toBe(badge.parentElement);
  expect(screen.queryByRole("tooltip")).toBeNull();
  expect(
    screen.getAllByRole("link", {
      name: "Source details in the instruments register",
    })[0],
  ).toHaveAttribute("href", "/submissions/instruments#SYNTHETIC");
});

it("keeps the floating disclosure inside a 320px viewport", () => {
  const width = Object.getOwnPropertyDescriptor(window, "innerWidth");
  Object.defineProperty(window, "innerWidth", {
    configurable: true,
    value: 320,
  });
  try {
    render(<InstrumentStatusBadge status="in_force" code="SYNTHETIC" />);
    fireEvent.click(screen.getByText("In force"));
    const panel = screen.getByTestId("instrument-status-disclosure");
    expect(panel).toHaveStyle({ position: "fixed", width: "288px" });
    expect(Number.parseFloat(panel.style.left)).toBeGreaterThanOrEqual(16);
    expect(
      Number.parseFloat(panel.style.left) +
        Number.parseFloat(panel.style.width),
    ).toBeLessThanOrEqual(304);
  } finally {
    if (width) Object.defineProperty(window, "innerWidth", width);
  }
});

/** Pins the badge's rect so the panel's placement can be asserted directly. */
function pinBadge(
  badge: HTMLElement,
  rect: { top: number; bottom: number; left: number },
) {
  badge.getBoundingClientRect = () =>
    ({
      ...rect,
      right: rect.left,
      width: 0,
      height: rect.bottom - rect.top,
      x: rect.left,
      y: rect.top,
      toJSON: () => ({}),
    }) as DOMRect;
}

function setViewportHeight(height: number) {
  const viewport = Object.getOwnPropertyDescriptor(window, "innerHeight");
  Object.defineProperty(window, "innerHeight", {
    configurable: true,
    value: height,
  });
  return () => {
    if (viewport) Object.defineProperty(window, "innerHeight", viewport);
  };
}

it("flips the floating disclosure above a badge near the bottom edge", () => {
  const restoreViewport = setViewportHeight(800);
  try {
    render(<InstrumentStatusBadge status="in_force" code="SYNTHETIC" />);
    const badge = screen.getByText("In force");
    pinBadge(badge, { top: 720, bottom: 748, left: 40 });
    fireEvent.click(badge);
    const panel = screen.getByTestId("instrument-status-disclosure");
    // Anchored to the badge's top edge with the gap, never below the fold.
    expect(panel.style.top).toBe("");
    expect(Number.parseFloat(panel.style.bottom)).toBeCloseTo(800 - 720 + 8);
    expect(Number.parseFloat(panel.style.maxHeight)).toBeLessThanOrEqual(
      720 - 8 - 16,
    );
    expect(panel.style.overflowY).toBe("auto");
  } finally {
    restoreViewport();
  }
});

it("scrolls the disclosure internally instead of running past the viewport", () => {
  const restoreViewport = setViewportHeight(800);
  try {
    render(<InstrumentStatusBadge status="in_force" code="SYNTHETIC" />);
    const badge = screen.getByText("In force");
    pinBadge(badge, { top: 100, bottom: 128, left: 40 });
    fireEvent.click(badge);
    const panel = screen.getByTestId("instrument-status-disclosure");
    const top = Number.parseFloat(panel.style.top);
    const maxHeight = Number.parseFloat(panel.style.maxHeight);
    expect(top).toBeCloseTo(128 + 8);
    expect(top + maxHeight).toBeLessThanOrEqual(800 - 16);
    expect(panel.style.overflowY).toBe("auto");
  } finally {
    restoreViewport();
  }
});

it.each([
  { top: -100, bottom: -80, anchor: "top" as const },
  { top: 880, bottom: 900, anchor: "bottom" as const },
])(
  "keeps the panel reachable after its badge scrolls beyond $anchor",
  (rect) => {
    const restoreViewport = setViewportHeight(800);
    try {
      render(<InstrumentStatusBadge status="in_force" code="SYNTHETIC" />);
      const badge = screen.getByText("In force");
      pinBadge(badge, { top: 100, bottom: 128, left: 40 });
      fireEvent.click(badge);
      pinBadge(badge, { ...rect, left: 40 });
      fireEvent.scroll(window);
      const panel = screen.getByTestId("instrument-status-disclosure");
      expect(badge.closest("details")).toHaveAttribute("open");
      const anchor = Number.parseFloat(panel.style[rect.anchor]);
      expect(anchor).toBe(16);
      expect(
        anchor + Number.parseFloat(panel.style.maxHeight),
      ).toBeLessThanOrEqual(784);
      expect(panel.style.overflowY).toBe("auto");
    } finally {
      restoreViewport();
    }
  },
);

it("dismisses the floating disclosure on Escape and outside press", () => {
  render(<InstrumentStatusBadge status="in_force" code="SYNTHETIC" />);
  const badge = screen.getByText("In force");
  const details = badge.closest("details")!;
  const panel = screen.getByTestId("instrument-status-disclosure");

  fireEvent.click(badge);
  expect(details.open).toBe(true);
  fireEvent.keyDown(document, { key: "Escape" });
  expect(details.open).toBe(false);
  expect(panel.style.position).toBe("");

  fireEvent.click(badge);
  expect(details.open).toBe(true);
  // A press inside the panel must not close it: the source link lives there.
  fireEvent.pointerDown(panel);
  expect(details.open).toBe(true);
  fireEvent.pointerDown(document.body);
  expect(details.open).toBe(false);
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
      expect(
        screen.getByTestId("instrument-status-disclosure"),
      ).toHaveTextContent(disclosure.tooltip);
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

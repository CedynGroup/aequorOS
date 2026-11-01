// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import LiveEngineNote from "../features/live/components/LiveEngineNote";
afterEach(cleanup);
it("distinguishes the dashboard reporting date from a later computation timestamp", () => {
  render(
    <LiveEngineNote
      dataAsOf={new Date("2026-09-30")}
      live={{ status: "green", computedAt: new Date("2026-10-20T12:00:00Z") }}
      stored={false}
    />,
  );
  expect(screen.getByText(/Dashboard reporting date:/)).toHaveTextContent(
    /Dashboard reporting date: 30 Sept? 2026. Calculated/,
  );
  expect(
    screen.getByTitle(/Calculation time, not the reporting date/),
  ).toBeVisible();
  expect(screen.queryByText(/Positions as of/)).not.toBeInTheDocument();
});
it("labels a stored dashboard dataset even when no live calculation timestamp exists", () => {
  render(<LiveEngineNote dataAsOf={new Date("2026-08-31")} stored />);
  expect(screen.getByText(/Dashboard reporting date:/)).toHaveTextContent(
    "31 Aug 2026. Computed from stored engine results",
  );
});

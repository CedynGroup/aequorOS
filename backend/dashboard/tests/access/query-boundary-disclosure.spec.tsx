// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import QueryBoundary from "@/components/ui/QueryBoundary";
import InstrumentStatusBadge from "@/components/submissions/InstrumentStatusBadge";

afterEach(cleanup);
it.each(["loading", "error"])(
  "preserves an open status disclosure when %s clears query data and recovers",
  async (state) => {
    const badge = (
      <InstrumentStatusBadge
        status="exposure_draft"
        citation="Original source"
        sourceUrl="https://example.test/source"
      />
    );
    const view = render(
      <QueryBoundary contained isLoading={false} error={null}>
        {badge}
      </QueryBoundary>,
    );
    await userEvent.click(screen.getByText("Draft"));
    expect(screen.getByTestId("instrument-status-disclosure")).toBeVisible();
    await act(async () =>
      view.rerender(
        <QueryBoundary
          contained
          isLoading={state === "loading"}
          error={
            state === "error" ? new Error("Temporary metadata failure") : null
          }
        >
          {null}
        </QueryBoundary>,
      ),
    );
    expect(
      screen.getByTestId("instrument-status-disclosure"),
    ).not.toBeVisible();
    await act(async () =>
      view.rerender(
        <QueryBoundary contained isLoading={false} error={null}>
          <InstrumentStatusBadge
            status="exposure_draft"
            citation="Updated source"
            sourceUrl="https://example.test/source"
          />
        </QueryBoundary>,
      ),
    );
    const disclosure = screen.getByTestId("instrument-status-disclosure");
    expect(disclosure).toBeVisible();
    expect(disclosure).toHaveTextContent("Updated source");
    expect(disclosure).not.toHaveTextContent("Original source");
  },
);

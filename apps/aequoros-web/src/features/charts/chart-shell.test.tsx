import { render, screen } from "@testing-library/react";
import { Component, type ReactNode } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ChartBoundary } from "./chart-shell";

class ThrowingChart extends Component<{ fail: boolean; children: ReactNode }> {
  render() {
    if (this.props.fail) throw new Error("Malformed chart data");
    return this.props.children;
  }
}

describe("ChartBoundary", () => {
  afterEach(() => vi.restoreAllMocks());

  it("recovers when the dataset reset key changes", () => {
    vi.spyOn(console, "error").mockImplementation(() => undefined);
    const { rerender } = render(
      <ChartBoundary title="Forecast trajectory" resetKey="run-1">
        <ThrowingChart fail>Chart ready</ThrowingChart>
      </ChartBoundary>,
    );

    expect(screen.getByText("Forecast trajectory unavailable")).toBeVisible();

    rerender(
      <ChartBoundary title="Forecast trajectory" resetKey="run-2">
        <ThrowingChart fail={false}>Chart ready</ThrowingChart>
      </ChartBoundary>,
    );

    expect(screen.getByText("Chart ready")).toBeVisible();
  });
});

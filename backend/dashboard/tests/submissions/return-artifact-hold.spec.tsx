// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { ArtifactKind } from "@aequoros/risk-service-api";
import { ArtifactGroup } from "@/components/submissions/ReturnCommandBar";

/**
 * The hold contract the artifact act lives by (SIGN-13): the act must not hand
 * over a document the version chain has not chosen yet. While the chain is
 * unresolved — pending or failed — every artifact control holds aria-disabled
 * with the reason reachable, and accepts no click.
 */

const CHAIN_PENDING = "The version chain has not chosen this return's document yet.";
const CHAIN_ERROR = "The version chain could not be loaded, so no document is chosen yet.";
const PREPARER_ONLY = "Producing it is the preparer's act.";

function renderArtifacts(overrides: {
  kinds?: ArtifactKind[];
  available?: string[];
  canExport?: boolean;
  pendingReason?: string | null;
  signedLabelFor?: (kind: ArtifactKind) => string | null;
}) {
  const onTake = vi.fn();
  render(
    <ArtifactGroup
      kinds={overrides.kinds ?? ["pdf"]}
      available={new Set(overrides.available ?? [])}
      busyKind={null}
      canExport={overrides.canExport ?? false}
      onTake={onTake}
      signedLabelFor={overrides.signedLabelFor}
      unavailableReason={PREPARER_ONLY}
      pendingReason={overrides.pendingReason}
    />,
  );
  return onTake;
}

function hoverReason(name: string): string | null {
  const control = screen.getByRole("button", { name });
  fireEvent.mouseEnter(control);
  const tooltip = document.querySelector('[role="tooltip"]');
  const text = tooltip?.textContent ?? null;
  fireEvent.mouseLeave(control);
  return text;
}

afterEach(cleanup);

describe("ArtifactGroup artifact hold", () => {
  it("holds every artifact control while the version chain is unresolved and accepts no click", () => {
    const onTake = renderArtifacts({
      kinds: ["pdf", "xlsx"],
      pendingReason: CHAIN_PENDING,
    });

    for (const name of ["PDF", "XLSX"]) {
      const control = screen.getByRole("button", { name });
      expect(control).toHaveAttribute("aria-disabled", "true");
      expect(hoverReason(name)).toBe(CHAIN_PENDING);
    }
    fireEvent.click(screen.getByRole("button", { name: "PDF" }));
    expect(onTake).not.toHaveBeenCalled();
  });

  it("lets the chain reason take precedence over the preparer's reason, including its error state", () => {
    for (const chainReason of [CHAIN_PENDING, CHAIN_ERROR]) {
      renderArtifacts({
        kinds: ["pdf"],
        canExport: false,
        pendingReason: chainReason,
      });
      expect(hoverReason("PDF")).toBe(chainReason);
      expect(hoverReason("PDF")).not.toBe(PREPARER_ONLY);
      cleanup();
    }
  });

  it("hands the produced kind over once the chain lands and holds only the missing kind", () => {
    const onTake = renderArtifacts({
      kinds: ["pdf", "xlsx"],
      available: ["pdf"],
      pendingReason: null,
      signedLabelFor: (kind) => (kind === "pdf" ? "Signed PDF" : null),
    });

    const ready = screen.getByRole("button", { name: "Download Signed PDF" });
    expect(ready).not.toHaveAttribute("aria-disabled", "true");
    fireEvent.click(ready);
    expect(onTake).toHaveBeenCalledWith("pdf");

    expect(hoverReason("XLSX")).toBe(PREPARER_ONLY);
    expect(onTake).toHaveBeenCalledTimes(1);
  });
});

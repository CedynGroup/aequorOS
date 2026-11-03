// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { PermissionButton } from "../components/ui/PermissionButton";

afterEach(cleanup);

it("keeps permission-denied actions disabled and explains them by keyboard", async () => {
  const clicked = vi.fn();
  const reason = "Requires Risk · Confidential · create.";
  render(
    <PermissionButton reason={reason} disabled={false} onClick={clicked}>
      New scenario
    </PermissionButton>,
  );
  const button = screen.getByRole("button", { name: "New scenario" });
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(clicked).not.toHaveBeenCalled();
  await userEvent.tab();
  expect(button).toHaveAccessibleDescription(reason);
  expect(screen.getByRole("tooltip")).toHaveTextContent(reason);
});

it("enables the same action once the permission reason is removed", () => {
  const clicked = vi.fn();
  const view = render(
    <PermissionButton reason="Requires create." onClick={clicked}>
      New scenario
    </PermissionButton>,
  );
  view.rerender(
    <PermissionButton onClick={clicked}>New scenario</PermissionButton>,
  );
  const button = screen.getByRole("button", { name: "New scenario" });
  expect(button).toBeEnabled();
  fireEvent.click(button);
  expect(clicked).toHaveBeenCalledOnce();
});

it("preserves ordinary disabled state without claiming a permission refusal", () => {
  render(<PermissionButton disabled>Saving</PermissionButton>);
  expect(screen.getByRole("button", { name: "Saving" })).toBeDisabled();
  expect(screen.queryByRole("tooltip")).not.toBeInTheDocument();
});

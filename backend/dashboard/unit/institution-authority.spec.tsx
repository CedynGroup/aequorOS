// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";
import React from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { EffectiveCapabilityRead } from "@aequoros/risk-service-api";
import { InstitutionAction } from "../components/institution/authority";

const context = vi.hoisted(() => ({
  institutionCapabilities: [] as EffectiveCapabilityRead[],
  authorityPending: false,
}));
vi.mock("../components/shell/BankContext", () => ({
  useBankContext: () => context,
}));
afterEach(() => {
  cleanup();
  context.institutionCapabilities = [];
  context.authorityPending = false;
});
function capability(
  sensitivity: "confidential" | "restricted",
  permission: "create" | "edit",
): EffectiveCapabilityRead {
  return {
    module: "institution",
    sensitivity,
    permission,
    requiresContextualAuthorization: false,
    dataScope: { kind: "all", branches: [], regions: [] },
  };
}
it("disables an ungranted edit and explains the exact grant by keyboard", async () => {
  const clicked = vi.fn();
  render(
    <InstitutionAction permission="edit" onClick={clicked}>
      Edit profile
    </InstitutionAction>,
  );
  const button = screen.getByRole("button", { name: "Edit profile" });
  expect(button).toBeDisabled();
  fireEvent.click(button);
  expect(clicked).not.toHaveBeenCalled();
  await userEvent.tab();
  expect(screen.getByRole("tooltip")).toHaveTextContent(
    "Institution · Confidential · Edit",
  );
});
it("requires the action and sensitivity on the same projected capability", () => {
  context.institutionCapabilities = [
    capability("restricted", "edit"),
    capability("confidential", "create"),
  ];
  const clicked = vi.fn();
  const view = render(
    <InstitutionAction permission="edit" onClick={clicked}>
      Edit profile
    </InstitutionAction>,
  );
  expect(screen.getByRole("button")).toBeDisabled();
  context.institutionCapabilities = [capability("confidential", "edit")];
  view.rerender(
    <InstitutionAction permission="edit" onClick={clicked}>
      Edit profile
    </InstitutionAction>,
  );
  expect(screen.getByRole("button")).toBeEnabled();
  fireEvent.click(screen.getByRole("button"));
  expect(clicked).toHaveBeenCalledOnce();
});
it("refuses contextual, partial-book and unresolved capabilities", () => {
  const view = render(
    <InstitutionAction permission="create">Add outlet</InstitutionAction>,
  );
  for (const row of [
    {
      ...capability("confidential", "create"),
      requiresContextualAuthorization: true,
    },
    {
      ...capability("confidential", "create"),
      dataScope: { kind: "branch" as const, branches: ["A"], regions: [] },
    },
  ]) {
    context.institutionCapabilities = [row];
    view.rerender(
      <InstitutionAction permission="create">Add outlet</InstitutionAction>,
    );
    expect(screen.getByRole("button")).toBeDisabled();
  }
  context.institutionCapabilities = [capability("confidential", "create")];
  context.authorityPending = true;
  view.rerender(
    <InstitutionAction permission="create">Add outlet</InstitutionAction>,
  );
  expect(screen.getByRole("button")).toBeDisabled();
});

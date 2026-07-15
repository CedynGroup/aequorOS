import { CaseSort } from "@aequoros/risk-service-api";
import { cleanup, render, screen } from "@testing-library/react";
import { page, userEvent } from "vitest/browser";
import { afterEach, describe, expect, it, vi } from "vitest";
import { act } from "react";

import {
  DEFAULT_ORG_ID,
  DEFAULT_TENANT_OPTIONS,
  DEFAULT_USER_ID,
} from "../../lib/constants";
import { renderWithQuery } from "../../test/render";
import { mockCaseList } from "../demo-data/demo-data";
import { BulkActionDialog } from "./bulk-actions";
import { TopBar } from "./shell";

type TopBarProps = Parameters<typeof TopBar>[0];

const tenant = {
  orgId: DEFAULT_ORG_ID,
  userId: DEFAULT_USER_ID,
};
const tenants = DEFAULT_TENANT_OPTIONS;

const cases = mockCaseList(
  DEFAULT_ORG_ID,
  {
    q: "",
    status: "all",
    risk: "all",
    archived: false,
    sort: CaseSort.UpdatedAtDesc,
  },
  1,
).items;

describe("risk console browser interactions", () => {
  afterEach(() => {
    cleanup();
  });

  it("selects the current case through the Radix select portal", async () => {
    const chooseCase = vi.fn<TopBarProps["chooseCase"]>();
    const chooseTenant = vi.fn<TopBarProps["chooseTenant"]>();

    render(
      <TopBar
        orgId={DEFAULT_ORG_ID}
        tenants={tenants}
        chooseTenant={chooseTenant}
        cases={cases}
        caseId={cases[0].id}
        chooseCase={chooseCase}
        queueVisible={false}
        toggleQueue={vi.fn<TopBarProps["toggleQueue"]>()}
        refresh={vi.fn<TopBarProps["refresh"]>()}
        seed={vi.fn<TopBarProps["seed"]>()}
      />,
    );

    await act(async () => {
      await userEvent.click(
        screen.getByRole("combobox", { name: "Organization" }),
      );
      await userEvent.click(
        screen.getByRole("option", { name: "AequorOS Isolated Tenant" }),
      );
      await userEvent.click(
        screen.getByRole("combobox", { name: "Current case" }),
      );
      await userEvent.click(
        screen.getByRole("option", { name: cases[1].title }),
      );
    });

    expect(chooseTenant).toHaveBeenCalledWith(
      "22222222-2222-4222-8222-222222222222",
    );
    expect(chooseCase).toHaveBeenCalledWith(cases[1].id);
  });

  it("keeps the case queue toggle accessible at a mobile viewport", async () => {
    await page.viewport(390, 844);
    const toggleQueue = vi.fn<TopBarProps["toggleQueue"]>();

    render(
      <TopBar
        orgId={DEFAULT_ORG_ID}
        tenants={tenants}
        chooseTenant={vi.fn<TopBarProps["chooseTenant"]>()}
        cases={cases}
        caseId={cases[0].id}
        chooseCase={vi.fn<TopBarProps["chooseCase"]>()}
        queueVisible={false}
        toggleQueue={toggleQueue}
        refresh={vi.fn<TopBarProps["refresh"]>()}
        seed={vi.fn<TopBarProps["seed"]>()}
      />,
    );

    const topBar = screen.getByTestId("risk-console-top-bar");
    const toggle = screen.getByRole("button", { name: "Show case queue" });
    const bounds = toggle.getBoundingClientRect();
    const hitTarget = document.elementFromPoint(
      bounds.left + bounds.width / 2,
      bounds.top + bounds.height / 2,
    );

    expect(topBar.scrollWidth).toBeLessThanOrEqual(topBar.clientWidth);
    expect(hitTarget === toggle || toggle.contains(hitTarget)).toBe(true);
    await userEvent.click(toggle);
    expect(toggleQueue).toHaveBeenCalledOnce();
  });

  it("opens the bulk action dialog in a real browser DOM", async () => {
    renderWithQuery(
      <BulkActionDialog selectedIds={[cases[0].id]} tenant={tenant} />,
    );

    await act(async () => {
      await userEvent.click(
        screen.getByRole("button", { name: "Bulk actions (1)" }),
      );
    });

    expect(screen.getByRole("dialog", { name: "Bulk actions" })).toBeVisible();
    expect(await screen.findByText("1 selected cases")).toBeVisible();
  });
});

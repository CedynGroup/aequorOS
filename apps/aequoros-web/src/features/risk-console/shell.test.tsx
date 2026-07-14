import { CaseSort, RiskLevel } from "@aequoros/risk-service-api";
import { fireEvent, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { DEFAULT_ORG_ID } from "../../lib/constants";
import { mockCaseList } from "../demo-data/demo-data";
import { Sidebar, TopBar } from "./shell";

type SidebarProps = Parameters<typeof Sidebar>[0];
type TopBarProps = Parameters<typeof TopBar>[0];

describe("risk console shell", () => {
  it("routes sidebar tab clicks through the provided handler", () => {
    const onTab = vi.fn<SidebarProps["onTab"]>();

    render(<Sidebar activeTab="overview" onTab={onTab} />);

    fireEvent.click(screen.getByRole("button", { name: "Reports" }));

    expect(onTab).toHaveBeenCalledWith("report");
  });

  it("wires named organization, case, queue, and demo controls in the top bar", async () => {
    const user = userEvent.setup();
    const chooseTenant = vi.fn<TopBarProps["chooseTenant"]>();
    const toggleQueue = vi.fn<TopBarProps["toggleQueue"]>();
    const seed = vi.fn<TopBarProps["seed"]>();
    const cases = mockCaseList(
      DEFAULT_ORG_ID,
      {
        q: "",
        status: "all",
        risk: RiskLevel.High,
        archived: false,
        sort: CaseSort.UpdatedAtDesc,
      },
      1,
    ).items;

    render(
      <TopBar
        orgId={DEFAULT_ORG_ID}
        chooseTenant={chooseTenant}
        cases={cases}
        caseId={cases[0].id}
        chooseCase={vi.fn<TopBarProps["chooseCase"]>()}
        queueVisible={false}
        toggleQueue={toggleQueue}
        refresh={vi.fn<TopBarProps["refresh"]>()}
        seed={seed}
      />,
    );

    await user.click(screen.getByRole("button", { name: "Show case queue" }));
    await user.click(screen.getByRole("button", { name: /Demo seed data/ }));

    expect(
      screen.getByRole("combobox", { name: "Current case" }),
    ).toHaveTextContent("Covenant review - Northstar Foods");
    expect(
      screen.getByRole("combobox", { name: "Organization" }),
    ).toHaveTextContent("AequorOS Demo Organization");
    expect(screen.queryByLabelText("Tenant org id")).toBeNull();
    expect(screen.queryByLabelText("User id")).toBeNull();
    expect(chooseTenant).not.toHaveBeenCalled();
    expect(toggleQueue).toHaveBeenCalledOnce();
    expect(seed).toHaveBeenCalledOnce();
  });
});
